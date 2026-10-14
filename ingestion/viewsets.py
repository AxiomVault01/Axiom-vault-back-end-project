from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiExample, OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers, status, viewsets
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response

from accounts.models import AuditLog
from accounts.permissions import PASSWORD_CHANGE_REQUIRED_MESSAGE, PasswordChangeNotRequired
from config.file_storage import StorageUnavailable

from .models import Payment, Payroll, Vendor
from .serializers import FileUploadSerializer
from .services.ingestion_service import MAX_ERRORS_SHOWN, MAX_ROWS, ROWS_INVALID_MESSAGE, IngestionService, UploadRejected
from .services.standardiser import PAYMENT_SCHEMA, PAYROLL_SCHEMA, VENDOR_SCHEMA

MAX_FILE_BYTES = 10 * 1024 * 1024
FILE_TOO_LARGE_MESSAGE = "The file is larger than 10 MB."
NO_ORGANISATION_MESSAGE = "Only organisation accounts can upload files."
STORAGE_UNAVAILABLE_MESSAGE = (
    "The file could not be stored right now. Nothing was saved. Try again in a few minutes."
)

RULE_TEXT = {
    "money": "amount, not negative, at most 2 decimals; commas, spaces, `₦`/`NGN` allowed",
    "count": "whole number, not negative",
    "account": "digits only (spaces and hyphens removed), at most 20",
    "date": "`2026-09-28`, `28/09/2026`, `28-09-2026` (day first), or an Excel date cell",
    "email": "email address",
    "phone": "7 to 15 digits, optionally starting with `+`",
}


class BelongsToOrganisation(BasePermission):
    message = {"error": NO_ORGANISATION_MESSAGE}

    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and request.user.organisation_id)


def _column_table(schema):
    lines = ["| Field | Required | Also accepted as | Rule |", "|---|---|---|---|"]
    for name, spec in schema.fields.items():
        rule = f"text, at most {spec.max_length} characters" if spec.kind == "text" else RULE_TEXT[spec.kind]
        if spec.blank is not None:
            rule += "; blank counts as 0"
        aliases = ", ".join(f"`{a}`" for a in spec.aliases)
        lines.append(f"| `{name}` | {'yes' if spec.required else 'no'} | {aliases} | {rule} |")
    return "\n".join(lines)


def _response_schema(kind_label):
    return inline_serializer(
        f"{kind_label.title()}UploadResponse",
        {
            "upload": inline_serializer(
                f"{kind_label.title()}UploadSummary",
                {
                    "id": serializers.UUIDField(help_text="The upload's ID. Its rows (and later its alerts) point to it."),
                    "kind": serializers.ChoiceField(
                        choices=["payroll", "vendors", "payments"], help_text="Which kind of file this was."
                    ),
                    "file_name": serializers.CharField(help_text="The name of the file that was sent."),
                    "row_count": serializers.IntegerField(help_text="How many rows were saved (blank rows are skipped)."),
                    "file_size": serializers.IntegerField(
                        help_text="Size of the original file in bytes. The file itself is kept privately as evidence."
                    ),
                    "created_at": serializers.DateTimeField(help_text="When the upload was saved (ISO 8601)."),
                },
            ),
            "ignored_columns": serializers.ListField(
                child=serializers.CharField(),
                help_text="Column names in the file that are not fields of this kind. They were not saved.",
            ),
        },
    )


def upload_docs(schema, label, screen_note, example_errors, duplicate_note, saved_example):
    """Full Swagger docs for one upload endpoint, built from its schema so they never drift apart."""
    return extend_schema(
        tags=["Ingestion"],
        summary=f"Upload a {label} file",
        description=(
            f"**Upload screen.** Sends one {label} file for the logged-in user's organisation. {screen_note} The "
            "API reads it, maps its columns to the standard fields, checks every row, and saves the whole file "
            "as one upload, or saves **nothing** if any row is wrong. The original file is kept privately as "
            "evidence.\n\n"
            "**Request:** `multipart/form-data` with one field, `file`: a `.csv` or `.xlsx` file of at most "
            f"10 MB and {MAX_ROWS:,} rows. Row 1 must hold the column names. In Excel, format account number "
            "columns as **Text** before typing, or numbers like `0123456789` lose the leading zero. Old `.xls` "
            "files are refused.\n\n"
            "**Columns.** Names are matched ignoring letter case, spaces, and hyphens. Other columns are "
            "ignored and listed in `ignored_columns`.\n\n"
            + _column_table(schema)
            + "\n\n**Other rules**\n"
            "- Text is trimmed. A completely blank row is skipped.\n"
            f"- {duplicate_note}\n\n"
            "**Responses**\n"
            "- `201`: saved. Show `row_count`, and warn about `ignored_columns` if it is not empty.\n"
            f"- `400`: nothing was saved. Either a file problem (`error`, and `missing_columns` when required "
            f"columns are absent), or row problems: `validation_errors` lists the first {MAX_ERRORS_SHOWN} "
            "(`row` is the spreadsheet row number, the header being row 1) and `error_count` is the total.\n"
            "- `401`: no or expired token. `403`: the account belongs to no organisation, or the user still has "
            "to change a temporary password. `413`: the file is too large. `503`: the file could not be stored "
            "and nothing was saved; try again in a few minutes.\n\n"
            "**Authentication:** `Authorization: Bearer <access_token>` of a super admin or team member."
        ),
        request={
            "multipart/form-data": {
                "type": "object",
                "properties": {"file": {"type": "string", "format": "binary", "description": "The .csv or .xlsx file."}},
                "required": ["file"],
            }
        },
        responses={
            201: OpenApiResponse(
                response=_response_schema(schema.kind),
                description="The whole file was saved.",
                examples=[OpenApiExample("Saved", value=saved_example)],
            ),
            400: OpenApiResponse(
                response=OpenApiTypes.OBJECT,
                description="Nothing was saved.",
                examples=[
                    OpenApiExample(
                        "Invalid rows",
                        value={"error": ROWS_INVALID_MESSAGE, "error_count": len(example_errors), "validation_errors": example_errors},
                    ),
                    OpenApiExample(
                        "Missing columns",
                        value={
                            "error": "Some required columns are missing. Check the column names in the first row.",
                            "missing_columns": schema.required_fields[-2:],
                        },
                    ),
                    OpenApiExample("Wrong file type", value={"error": "Upload a .csv or .xlsx file."}),
                    OpenApiExample("No file", value={"file": ["No file was submitted."]}),
                ],
            ),
            401: OpenApiResponse(
                response=OpenApiTypes.OBJECT,
                examples=[OpenApiExample("No token", value={"detail": "Authentication credentials were not provided."})],
            ),
            403: OpenApiResponse(
                response=OpenApiTypes.OBJECT,
                examples=[
                    OpenApiExample("No organisation", value={"error": NO_ORGANISATION_MESSAGE}),
                    OpenApiExample(
                        "Temporary password",
                        value={"error": PASSWORD_CHANGE_REQUIRED_MESSAGE, "code": "password_change_required"},
                    ),
                ],
            ),
            413: OpenApiResponse(
                response=OpenApiTypes.OBJECT,
                examples=[OpenApiExample("Too large", value={"error": FILE_TOO_LARGE_MESSAGE})],
            ),
            503: OpenApiResponse(
                response=OpenApiTypes.OBJECT,
                description="The original file could not be stored. Nothing was saved.",
                examples=[OpenApiExample("Storage unavailable", value={"error": STORAGE_UNAVAILABLE_MESSAGE})],
            ),
        },
    )


def _saved(kind, name, rows, size, ignored):
    return {
        "upload": {
            "id": "9b2f6c1e-4d3a-4f7b-8e2d-1a5c7b9e0f3d",
            "kind": kind,
            "file_name": name,
            "row_count": rows,
            "file_size": size,
            "created_at": "2026-10-14T10:15:00Z",
        },
        "ignored_columns": ignored,
    }


class IngestionViewSet(viewsets.ViewSet):
    parser_classes = [MultiPartParser, FormParser]
    permission_classes = [IsAuthenticated, PasswordChangeNotRequired, BelongsToOrganisation]

    def _upload(self, request, schema, row_model, audit_action):
        serializer = FileUploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        file = serializer.validated_data["file"]
        if file.size > MAX_FILE_BYTES:
            return Response({"error": FILE_TOO_LARGE_MESSAGE}, status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)

        try:
            upload, ignored_columns = IngestionService.upload(file, request.user, schema, row_model)
        except UploadRejected as exc:
            return Response(exc.body, status=status.HTTP_400_BAD_REQUEST)
        except StorageUnavailable:
            return Response({"error": STORAGE_UNAVAILABLE_MESSAGE}, status=status.HTTP_503_SERVICE_UNAVAILABLE)

        AuditLog.record(
            audit_action,
            actor=request.user,
            target=upload,
            details={"file_name": upload.file_name, "row_count": upload.row_count},
            request=request,
        )
        return Response(
            {
                "upload": {
                    "id": str(upload.id),
                    "kind": upload.kind,
                    "file_name": upload.file_name,
                    "row_count": upload.row_count,
                    "file_size": upload.file_size,
                    "created_at": upload.created_at,
                },
                "ignored_columns": ignored_columns,
            },
            status=status.HTTP_201_CREATED,
        )

    @upload_docs(
        PAYROLL_SCHEMA,
        "payroll",
        "One row per employee per pay period.",
        [
            {"row": 5, "field": "net_salary", "error": "The amount cannot be negative."},
            {"row": 9, "field": "payment_date", "error": "Use a date like 2026-09-28 or 28/09/2026."},
        ],
        "The same employee, account number, and pay period twice in one file is an error.",
        _saved("payroll", "september-payroll.xlsx", 248, 48213, ["Remarks"]),
    )
    def upload_payroll(self, request):
        return self._upload(request, PAYROLL_SCHEMA, Payroll, AuditLog.PAYROLL_UPLOADED)

    @upload_docs(
        VENDOR_SCHEMA,
        "vendors",
        "One row per vendor (supplier). Each upload is a full snapshot of the vendor list.",
        [
            {"row": 4, "field": "account_number", "error": "Use digits only, at most 20."},
            {"row": 7, "field": "email", "error": "Enter a valid email address."},
        ],
        "The same `vendor_id` twice in one file is an error.",
        _saved("vendors", "vendors-october.csv", 64, 9120, []),
    )
    def upload_vendors(self, request):
        return self._upload(request, VENDOR_SCHEMA, Vendor, AuditLog.VENDORS_UPLOADED)

    @upload_docs(
        PAYMENT_SCHEMA,
        "payments",
        "One row per payment made to a vendor. `vendor_id` links a payment to the vendors file.",
        [
            {"row": 3, "field": "amount", "error": "Enter an amount like 250000 or 250,000.00."},
            {"row": 6, "field": "transaction_id", "error": "Duplicate payment: the same transaction_id as row 2."},
        ],
        "The same `transaction_id` twice in one file is an error. The same invoice paid twice is **not** "
        "an error here: it is what fraud detection looks for.",
        _saved("payments", "payments-october.xlsx", 512, 61440, ["Remarks"]),
    )
    def upload_payments(self, request):
        return self._upload(request, PAYMENT_SCHEMA, Payment, AuditLog.PAYMENTS_UPLOADED)
