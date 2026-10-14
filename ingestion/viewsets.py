from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiExample, OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers, status, viewsets
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response

from accounts.models import AuditLog
from accounts.permissions import PASSWORD_CHANGE_REQUIRED_MESSAGE, PasswordChangeNotRequired

from .serializers import PayrollUploadSerializer
from .services.ingestion_service import MAX_ERRORS_SHOWN, MAX_ROWS, ROWS_INVALID_MESSAGE, IngestionService, UploadRejected
from .services.standardiser import ALIASES, REQUIRED_FIELDS

MAX_FILE_BYTES = 10 * 1024 * 1024
FILE_TOO_LARGE_MESSAGE = "The file is larger than 10 MB."
NO_ORGANISATION_MESSAGE = "Only organisation accounts can upload payroll."


class BelongsToOrganisation(BasePermission):
    message = {"error": NO_ORGANISATION_MESSAGE}

    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and request.user.organisation_id)


def _column_table():
    lines = ["| Field | Required | Also accepted as |", "|---|---|---|"]
    for field, names in ALIASES.items():
        required = "yes" if field in REQUIRED_FIELDS else "no"
        lines.append(f"| `{field}` | {required} | {', '.join(f'`{n}`' for n in names)} |")
    return "\n".join(lines)


UploadResponse = inline_serializer(
    "PayrollUploadResponse",
    {
        "upload": inline_serializer(
            "PayrollUploadSummary",
            {
                "id": serializers.UUIDField(help_text="The upload's ID. Its rows and, later, its alerts point to it."),
                "file_name": serializers.CharField(help_text="The name of the file that was sent."),
                "row_count": serializers.IntegerField(help_text="How many payroll rows were saved (blank rows are skipped)."),
                "created_at": serializers.DateTimeField(help_text="When the upload was saved (ISO 8601)."),
            },
        ),
        "ignored_columns": serializers.ListField(
            child=serializers.CharField(),
            help_text="Column names in the file that are not payroll fields. They were not saved.",
        ),
    },
)


class IngestionViewSet(viewsets.ViewSet):
    parser_classes = [MultiPartParser, FormParser]
    permission_classes = [IsAuthenticated, PasswordChangeNotRequired, BelongsToOrganisation]

    @extend_schema(
        tags=["Ingestion"],
        summary="Upload a payroll file",
        description=(
            "**Upload screen.** Sends one payroll file for the logged-in user's organisation. The API "
            "reads it, maps its columns to the standard payroll fields, checks every row, and saves the "
            "whole file as one upload, or saves **nothing** if any row is wrong.\n\n"
            "**Request:** `multipart/form-data` with one field, `file`: a `.csv` or `.xlsx` file of at most "
            f"10 MB and {MAX_ROWS:,} rows. Row 1 must hold the column names. In Excel, format the account "
            "number column as **Text** before typing, or numbers like `0123456789` lose the leading zero. "
            "Old `.xls` files are refused.\n\n"
            "**Columns.** Names are matched ignoring letter case, spaces, and hyphens (`Net Pay` = `net_pay`). "
            "Other columns are ignored and listed in `ignored_columns`.\n\n"
            + _column_table()
            + "\n\n**Value rules**\n"
            "- Money (`salary_basic`, `net_salary`, `allowances`, `deductions`): a number with at most 2 "
            "decimal places, not negative. Commas, spaces, and a leading `₦` or `NGN` are allowed. Blank "
            "allowances or deductions count as 0.\n"
            "- `account_number`: digits only (spaces and hyphens are removed), at most 20.\n"
            "- `payment_date`: `2026-09-28`, `28/09/2026`, `28-09-2026` (day first), or an Excel date cell.\n"
            "- `attendance_days`, `leave_days`: a whole number, or blank.\n"
            "- Text fields are trimmed and must fit their length (`pay_period` 20, `employee_id` 100, names 255).\n"
            "- A completely blank row is skipped.\n"
            "- The same employee, account number, and pay period twice in one file is an error.\n\n"
            "**Responses**\n"
            "- `201`: saved. Show `row_count`, and warn about `ignored_columns` if it is not empty.\n"
            f"- `400`: nothing was saved. Either a file problem (`error`, and `missing_columns` when required "
            f"columns are absent), or row problems: `validation_errors` lists the first {MAX_ERRORS_SHOWN} "
            "(`row` is the spreadsheet row number, the header being row 1), and `error_count` is the total. "
            "Show them so the user can fix the file and upload again.\n"
            "- `401`: no or expired token. `403`: the account belongs to no organisation, or the user still "
            "has to change a temporary password. `413`: the file is too large.\n\n"
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
                response=UploadResponse,
                description="The whole file was saved.",
                examples=[
                    OpenApiExample(
                        "Saved",
                        value={
                            "upload": {
                                "id": "9b2f6c1e-4d3a-4f7b-8e2d-1a5c7b9e0f3d",
                                "file_name": "september-payroll.xlsx",
                                "row_count": 248,
                                "created_at": "2026-10-13T10:15:00Z",
                            },
                            "ignored_columns": ["Remarks"],
                        },
                    )
                ],
            ),
            400: OpenApiResponse(
                response=OpenApiTypes.OBJECT,
                description="Nothing was saved.",
                examples=[
                    OpenApiExample(
                        "Invalid rows",
                        value={
                            "error": ROWS_INVALID_MESSAGE,
                            "error_count": 2,
                            "validation_errors": [
                                {"row": 5, "field": "net_salary", "error": "The amount cannot be negative."},
                                {"row": 9, "field": "payment_date", "error": "Use a date like 2026-09-28 or 28/09/2026."},
                            ],
                        },
                    ),
                    OpenApiExample(
                        "Missing columns",
                        value={
                            "error": "Some required columns are missing. Check the column names in the first row.",
                            "missing_columns": ["account_number", "pay_period"],
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
        },
    )
    def upload_payroll(self, request):
        serializer = PayrollUploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        file = serializer.validated_data["file"]
        if file.size > MAX_FILE_BYTES:
            return Response({"error": FILE_TOO_LARGE_MESSAGE}, status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)

        try:
            upload, ignored_columns = IngestionService.upload_payroll(file, request.user)
        except UploadRejected as exc:
            return Response(exc.body, status=status.HTTP_400_BAD_REQUEST)

        AuditLog.record(
            AuditLog.PAYROLL_UPLOADED,
            actor=request.user,
            target=upload,
            details={"file_name": upload.file_name, "row_count": upload.row_count},
            request=request,
        )
        return Response(
            {
                "upload": {
                    "id": str(upload.id),
                    "file_name": upload.file_name,
                    "row_count": upload.row_count,
                    "created_at": upload.created_at,
                },
                "ignored_columns": ignored_columns,
            },
            status=status.HTTP_201_CREATED,
        )
