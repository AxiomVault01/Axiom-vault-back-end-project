"""Admin Control Panel: Audit Log screen (feature 25, ACP-04)."""

import csv
import json
from datetime import datetime, time, timedelta, timezone as dt_timezone

from django.db.models import Max, Q
from django.http import Http404, StreamingHttpResponse
from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .admin_serializers import (
    AuditActionSerializer,
    AuditLogEntrySerializer,
    AuditLogFilterSerializer,
    AuditVerifySerializer,
)
from .admin_viewsets import ADMIN_401, ADMIN_403, ADMIN_AUTH_NOTE, ADMIN_TAG, _paged
from .models import AuditLog
from .permissions import IsAxiomVaultStaff, PasswordChangeNotRequired
from .services import AuditLogService

CSV_COLUMNS = [
    "sequence", "created_at", "action", "actor_email", "organisation_name", "target_type", "target_id", "ip_address",
    "details", "previous_hash", "hash",
]
# A spreadsheet runs a cell starting with one of these as a formula.
FORMULA_STARTS = ("=", "+", "-", "@", "\t", "\r")

ENTRY_EXAMPLE = {
    "id": "6f1c2d3e-4a5b-4c6d-8e9f-0a1b2c3d4e5f",
    "sequence": 812,
    "created_at": "2026-10-22T18:04:11.204Z",
    "action": "case_report_exported",
    "action_label": "Exported a case report",
    "actor": {"id": "2d089ad3-9545-4611-a9dc-731c3a81879f", "email": "ngozi@ikejacoop.ng", "full_name": "Ngozi Okafor"},
    "organisation": {"id": "b3f1c2d4-5e6f-4a7b-8c9d-0e1f2a3b4c5d", "name": "Ikeja Coop"},
    "target_type": "case",
    "target_id": "9e8d7c6b-5a4f-4e3d-8c2b-1a0f9e8d7c6b",
    "details": {"reference": "CASE-0001", "format": "pdf"},
    "ip_address": "102.89.34.10",
    "previous_hash": "4b6a2f0c9d1e8f7a6b5c4d3e2f1a0b9c8d7e6f5a4b3c2d1e0f9a8b7c6d5e4f3a",
    "hash": "9c1d2e3f4a5b6c7d8e9f0a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d",
}
FILTER_NOTE = (
    "**Filters** (all optional, combined with AND):\n"
    "- `action`: one action code (`GET .../audit-log/actions/` lists them)\n"
    "- `organisation`: an organisation ID, or `none` for platform actions with no organisation (staff, admin panel)\n"
    "- `actor`: a user ID\n"
    "- `target_type` and `target_id`: the record acted on, e.g. `target_type=case`\n"
    "- `date_from`, `date_to`: `YYYY-MM-DD`, inclusive, in UTC\n"
    "- `search`: part of the actor's email, the organisation name, or the target ID, letter case ignored\n\n"
    "An unknown action, a malformed ID or date, or `date_from` after `date_to` is 400."
)
FILTER_PARAMS = [
    OpenApiParameter("action", OpenApiTypes.STR, description="Action code, e.g. `login`."),
    OpenApiParameter("organisation", OpenApiTypes.STR, description="Organisation ID, or `none`."),
    OpenApiParameter("actor", OpenApiTypes.UUID, description="The acting user's ID."),
    OpenApiParameter("target_type", OpenApiTypes.STR, description="Kind of record, e.g. `case`."),
    OpenApiParameter("target_id", OpenApiTypes.STR, description="ID of the record."),
    OpenApiParameter("date_from", OpenApiTypes.DATE, description="From this day (UTC), inclusive."),
    OpenApiParameter("date_to", OpenApiTypes.DATE, description="Up to this day (UTC), inclusive."),
    OpenApiParameter("search", OpenApiTypes.STR, description="Part of the actor email, organisation name, or target ID."),
]
BAD_FILTER = OpenApiResponse(
    response=OpenApiTypes.OBJECT,
    examples=[
        OpenApiExample("Unknown action", value={"action": ['"hack" is not a valid choice.']}),
        OpenApiExample("Dates reversed", value={"date_to": ["Must be on or after date_from."]}),
        OpenApiExample("Bad organisation", value={"organisation": ["Use an organisation ID or `none`."]}),
    ],
)


def _day_start(day):
    return datetime.combine(day, time.min, tzinfo=dt_timezone.utc)


def _csv_cell(value):
    text = "" if value is None else str(value)
    return "'" + text if text.startswith(FORMULA_STARTS) else text


class _Echo:
    """A file-like object whose write() returns the line, so csv.writer can feed a streaming response."""

    def write(self, value):
        return value


class AuditLogAdminViewSet(viewsets.GenericViewSet):
    """Audit Log screen: view, filter, verify, and export."""

    permission_classes = [IsAuthenticated, PasswordChangeNotRequired, IsAxiomVaultStaff]
    serializer_class = AuditLogEntrySerializer
    queryset = AuditLog.objects.none()  # Only tells Swagger the model; requests use get_queryset().
    lookup_value_regex = "[0-9a-fA-F-]{36}"

    def get_queryset(self):
        return AuditLog.objects.select_related("actor").order_by("-sequence")

    def _filtered(self, request):
        filters = AuditLogFilterSerializer(data=request.query_params)
        filters.is_valid(raise_exception=True)
        f = filters.validated_data
        rows = self.get_queryset()
        for name in ("action", "target_type", "target_id"):
            if f.get(name):
                rows = rows.filter(**{name: f[name]})
        if f.get("organisation"):
            rows = rows.filter(organisation__isnull=True) if f["organisation"] == "none" else rows.filter(organisation_id=f["organisation"])
        if f.get("actor"):
            rows = rows.filter(actor_id=f["actor"])
        if f.get("date_from"):
            rows = rows.filter(created_at__gte=_day_start(f["date_from"]))
        if f.get("date_to"):
            rows = rows.filter(created_at__lt=_day_start(f["date_to"] + timedelta(days=1)))
        if f.get("search", "").strip():
            text = f["search"].strip()
            rows = rows.filter(Q(actor_email__icontains=text) | Q(organisation_name__icontains=text) | Q(target_id__icontains=text))
        return rows, {key: str(value) for key, value in f.items()}

    @extend_schema(
        tags=[ADMIN_TAG],
        summary="List the audit log",
        description=(
            "**Audit Log screen** of the Admin Control Panel. Every recorded action across the platform, newest "
            "first: sign-ups, logins and logouts, password changes, team and admin-panel account changes, uploads, "
            "alerts, cases, evidence, and exports. Each entry says who did it, in which organisation, to which record, "
            "when, and from which IP address.\n\n" + FILTER_NOTE + "\n\n"
            "**Paginated:** 20 a page; `?page=2`, `?page_size=` up to 100. The body is `{count, next, previous, "
            "results}`.\n\nViewing the log is not itself recorded; exporting it is.\n\n" + ADMIN_AUTH_NOTE
        ),
        parameters=[
            *FILTER_PARAMS,
            OpenApiParameter("page", OpenApiTypes.INT, description="Page number, from 1."),
            OpenApiParameter("page_size", OpenApiTypes.INT, description="Items per page, default 20, at most 100."),
        ],
        responses={
            200: OpenApiResponse(response=AuditLogEntrySerializer(many=True), examples=[OpenApiExample("Audit log", value=_paged([ENTRY_EXAMPLE]))]),
            400: BAD_FILTER, 401: ADMIN_401, 403: ADMIN_403,
        },
    )
    def list(self, request):
        rows, _ = self._filtered(request)
        page = self.paginate_queryset(rows)
        return self.get_paginated_response(AuditLogEntrySerializer(page, many=True).data)

    @extend_schema(
        tags=[ADMIN_TAG],
        summary="View one audit log entry",
        description="One entry, with its full `details`, `previous_hash`, and `hash`.\n\n" + ADMIN_AUTH_NOTE,
        parameters=[OpenApiParameter("id", OpenApiTypes.UUID, OpenApiParameter.PATH, description="The entry's ID.")],
        responses={
            200: OpenApiResponse(response=AuditLogEntrySerializer, examples=[OpenApiExample("Entry", value=ENTRY_EXAMPLE)]),
            401: ADMIN_401, 403: ADMIN_403,
            404: OpenApiResponse(response=OpenApiTypes.OBJECT, examples=[OpenApiExample("Not found", value={"detail": "No audit log entry with this ID."})]),
        },
    )
    def retrieve(self, request, pk=None):
        row = self.get_queryset().filter(pk=pk).first()
        if row is None:
            raise Http404("No audit log entry with this ID.")
        return Response(AuditLogEntrySerializer(row).data, status=status.HTTP_200_OK)

    @extend_schema(
        tags=[ADMIN_TAG],
        summary="Audit log action types",
        description="Every action code with its readable name, for the action filter dropdown.\n\n" + ADMIN_AUTH_NOTE,
        responses={
            200: OpenApiResponse(
                response=AuditActionSerializer(many=True),
                examples=[OpenApiExample("Actions", value=[{"value": "signup", "label": "Signed up"}, {"value": "login", "label": "Logged in"}])],
            ),
            401: ADMIN_401, 403: ADMIN_403,
        },
    )
    @action(detail=False, methods=["get"])
    def actions(self, request):
        return Response([{"value": value, "label": label} for value, label in AuditLog.ACTION_CHOICES], status=status.HTTP_200_OK)

    @extend_schema(
        tags=[ADMIN_TAG],
        summary="Verify the audit log",
        description=(
            "**Verify button.** Proves the log was not altered. Every entry stores a SHA-256 `hash` of its own "
            "content plus the `hash` of the entry before it, and entries are numbered 1, 2, 3, ... (`sequence`). This "
            "check recomputes every hash in order and stops at the first problem:\n"
            "- `altered`: an entry's content was changed after it was written\n"
            "- `missing`: entries were deleted (a gap in the numbers, or entries missing at the end)\n"
            "- `broken_link`: an entry was changed and re-hashed, so the next entry no longer links to it\n\n"
            "`valid: true` means the whole chain is intact. Show `checked`, `last_sequence`, and `last_hash`; keeping "
            "`last_hash` somewhere outside the system lets a later check prove nothing before it changed.\n\n"
            "**Limit:** this catches changes made directly in the database without rebuilding the chain. Someone able "
            "to rewrite the whole table and its chain could hide a change; comparing against a `last_hash` kept "
            "outside the system catches that too.\n\n"
            "It reads the whole log, so it can take a few seconds on a large log. Nothing is changed.\n\n" + ADMIN_AUTH_NOTE
        ),
        responses={
            200: OpenApiResponse(
                response=AuditVerifySerializer,
                examples=[
                    OpenApiExample("Intact", value={"valid": True, "checked": 812, "last_sequence": 812, "last_hash": ENTRY_EXAMPLE["hash"], "problem": None, "verified_at": "2026-10-23T09:00:00Z"}),
                    OpenApiExample("Altered", value={
                        "valid": False, "checked": 40, "last_sequence": 40, "last_hash": ENTRY_EXAMPLE["previous_hash"],
                        "problem": {"sequence": 41, "id": ENTRY_EXAMPLE["id"], "reason": "altered", "detail": "Row 41: Its content no longer matches its hash."},
                        "verified_at": "2026-10-23T09:00:00Z",
                    }),
                ],
            ),
            401: ADMIN_401, 403: ADMIN_403,
        },
    )
    @action(detail=False, methods=["get"])
    def verify(self, request):
        return Response(AuditVerifySerializer(AuditLogService.verify()).data, status=status.HTTP_200_OK)

    @extend_schema(
        tags=[ADMIN_TAG],
        summary="Export the audit log (CSV)",
        description=(
            "**Export button.** Downloads every entry matching the filters as a CSV file, newest first, named "
            "`axiomvault-audit-log-YYYY-MM-DD.csv`. Columns: `sequence, created_at, action, actor_email, "
            "organisation_name, target_type, target_id, ip_address, details` (JSON), `previous_hash, hash`. A cell "
            "that starts with `=`, `+`, `-`, or `@` gets a leading `'` so a spreadsheet shows it as text instead of "
            "running it.\n\n" + FILTER_NOTE + "\n\n"
            "**Recorded:** each export writes one `audit_log_exported` entry with the filters used and the row "
            "count, so you can see who took the log out of the system.\n\n"
            "Fetch it with the Bearer token and save the blob (a plain link cannot send the token). Leave `Accept` "
            "out or send `*/*`. Errors are JSON.\n\n" + ADMIN_AUTH_NOTE
        ),
        parameters=FILTER_PARAMS,
        responses={
            (200, "text/csv"): OpenApiResponse(response=OpenApiTypes.BINARY, description="The CSV file."),
            400: BAD_FILTER, 401: ADMIN_401, 403: ADMIN_403,
        },
    )
    @action(detail=False, methods=["get"])
    def export(self, request):
        rows, filters = self._filtered(request)
        # The file holds exactly what existed when it was asked for, even while new entries keep arriving.
        newest = AuditLog.objects.aggregate(top=Max("sequence"))["top"] or 0
        rows = rows.filter(sequence__lte=newest)
        AuditLog.record(AuditLog.AUDIT_LOG_EXPORTED, actor=request.user, request=request, details={"filters": filters, "rows": rows.count()})
        writer = csv.writer(_Echo())

        def lines():
            yield writer.writerow(CSV_COLUMNS)
            for row in rows.iterator(chunk_size=2000):
                yield writer.writerow([_csv_cell(value) for value in (
                    row.sequence, row.created_at.isoformat(), row.action, row.actor_email, row.organisation_name,
                    row.target_type, row.target_id, row.ip_address, json.dumps(row.details, sort_keys=True),
                    row.previous_hash, row.hash,
                )])

        response = StreamingHttpResponse(lines(), content_type="text/csv; charset=utf-8", status=status.HTTP_200_OK)
        name = f"axiomvault-audit-log-{timezone.now().date().isoformat()}.csv"
        response["Content-Disposition"] = f'attachment; filename="{name}"'
        return response
