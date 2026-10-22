import re

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Q
from django.http import Http404
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from accounts.models import AuditLog
from accounts.permissions import PASSWORD_CHANGE_REQUIRED_MESSAGE, PasswordChangeNotRequired
from ingestion.viewsets import BelongsToOrganisation, NO_ORGANISATION_MESSAGE

from .investigation_serializers import InvestigationSerializer
from .models import Alert, AlertEvent, AlertNote
from .services.investigation import InvestigationService
from .serializers import (
    AlertDetailSerializer,
    AlertEventSerializer,
    AlertListFilterSerializer,
    AlertListSerializer,
    AlertNoteSerializer,
    ReviewerSerializer,
    StatusChangeSerializer,
)

User = get_user_model()
REFERENCE_PATTERN = re.compile(r"ALT-(\d{4,})", re.IGNORECASE)
REVIEWER_INVALID_MESSAGE = "Choose an active member of your organisation."

AUTH_NOTE = (
    "**Authentication:** `Authorization: Bearer <access_token>` of any member of the organisation (super admin "
    "or team member). Only the caller's own organisation's alerts exist for them; any other reference is 404."
)
PAGINATION_NOTE = (
    "**Paginated:** 20 a page by default; `?page=2`, `?page_size=` up to 100. The body is "
    "`{count, next, previous, results}`; a page past the end is 404."
)
ALERT_EXAMPLE = {
    "reference": "ALT-0002",
    "risk_type": "Duplicate Payment",
    "rule": "duplicate_payment",
    "title": "Invoice INV-2024-8847 paid 2 times to Acme Supplies",
    "entity_type": "vendor",
    "entity_name": "Acme Supplies",
    "entity_ref": "VND-8847",
    "department": "Procurement",
    "severity": "high",
    "status": "new",
    "risk_score": 90,
    "confidence": 90,
    "assigned_reviewer": None,
    "detected_at": "2026-10-14T18:02:11Z",
}
DETAIL_EXAMPLE = {
    **ALERT_EXAMPLE,
    "explanation": "Invoice INV-2024-8847 from Acme Supplies was paid 2 times under different transactions "
    "(TXN-90214, TXN-90455), 84,700.00 in total. 42,350.00 is more than the invoice should have cost and may need "
    "to be recovered.",
    "confidence_source": "derived",
    "flagged_records": [
        {"transaction_id": "TXN-90214", "payment_date": "2026-05-05", "amount": "42,350.00", "invoice_number": "INV-2024-8847", "approver": "John Doe"},
        {"transaction_id": "TXN-90455", "payment_date": "2026-05-08", "amount": "42,350.00", "invoice_number": "INV-2024-8847", "approver": "John Doe"},
    ],
    "timeline": [
        {"date": "2026-05-05", "label": "Payment", "detail": "TXN-90214: paid 42,350.00"},
        {"date": "2026-05-08", "label": "Payment", "detail": "TXN-90455: paid 42,350.00"},
        {"date": "2026-05-08", "label": "Alert generated", "detail": "Duplicate payment detected"},
    ],
    "evidence": [],
    "currency": "NGN",
    "upload": {"id": "e8a95cf3-546e-4177-8475-71c91f873e49", "kind": "payments", "file_name": "payments.xlsx", "created_at": "2026-10-14T18:02:10Z"},
    "notes_count": 0,
    "case": None,
    "ai_summary": "",
    "ai_recommendations": [],
    "pattern_detections": [],
    "ai_confidence": None,
    "updated_at": "2026-10-14T18:02:11Z",
}


INVESTIGATION_EXAMPLE = {
    "subject": {"type": "alert", "reference": "ALT-0002"},
    "alerts": [ALERT_EXAMPLE],
    "related": {
        "employees": [],
        "vendors": [{"id": "VND-8847", "name": "Acme Supplies", "bank_name": "GTBank", "registration_number": "RC-1029384"}],
        "approvers": [{"id": "", "name": "John Doe", "role": "", "department": ""}],
    },
    "linked_alerts": [{**ALERT_EXAMPLE, "reference": "ALT-0007", "title": "Invoice INV-2024-9120 paid 2 times to Acme Supplies", "case": "CASE-0003"}],
    "transactions": {
        "count": 3,
        "results": [
            {"kind": "payment", "id": "TXN-91002", "description": "Acme Supplies, invoice INV-2024-9001", "amount": "18,000.00", "date": "2026-06-01", "status": "clear", "alerts": []},
            {"kind": "payment", "id": "TXN-90455", "description": "Acme Supplies, invoice INV-2024-8847", "amount": "42,350.00", "date": "2026-05-08", "status": "flagged", "alerts": ["ALT-0002"]},
            {"kind": "payment", "id": "TXN-90214", "description": "Acme Supplies, invoice INV-2024-8847", "amount": "42,350.00", "date": "2026-05-05", "status": "flagged", "alerts": ["ALT-0002"]},
        ],
    },
    "analysis": {
        "total_exposure": "84,700.00", "duplicate_amount": "42,350.00", "risk_score": 90, "transaction_count": 2,
        "currency": "NGN", "source": "derived",
    },
    "pattern_detections": [],
    "timeline": [
        {"date": "2026-05-05", "label": "Payment", "detail": "TXN-90214: paid 42,350.00", "alert": "ALT-0002"},
        {"date": "2026-05-08", "label": "Payment", "detail": "TXN-90455: paid 42,350.00", "alert": "ALT-0002"},
        {"date": "2026-05-08", "label": "Alert generated", "detail": "Duplicate payment detected", "alert": "ALT-0002"},
    ],
}
INVESTIGATION_NOTE = (
    "**What each part means:**\n"
    "- `related`: the employees the alerts are about or flag (name, position, department from their latest "
    "payroll row), the vendors (name, bank, registration number from the latest vendors file), and the approvers "
    "of the flagged records (a payroll `approver_id` is looked up as an employee; a payment approver is a name "
    "only). Bank account numbers are not repeated here; the flagged records already show them.\n"
    "- `linked_alerts`: other alerts of your organisation about the same employees or vendors, newest first, at "
    "most 50, each with the `case` investigating it. Open one with `GET /api/v1/fraud/alerts/{reference}/`.\n"
    "- `transactions`: connected transactions, every stored vendor payment to those vendors and every salary paid "
    "to those employees or into the flagged bank accounts. `count` is the total; `results` holds the newest 100. "
    "`status` is `flagged` (with the `alerts` that flag it) or `clear`.\n"
    "- `analysis`: the Transaction Analysis box. `total_exposure` sums the flagged amounts (each transaction once); "
    "`duplicate_amount` is the money paid in duplicate (duplicate payment: total minus the largest payment; shared "
    "bank account: every salary after the first; 0 for other rules); `risk_score` is the highest alert score; "
    "`transaction_count` counts the flagged transactions. `source: \"derived\"` until ML defines these numbers.\n"
    "- `pattern_detections`: reserved for ML, `[]` for now.\n"
    "- `timeline`: the transaction timeline (payments, salaries, alert generated), oldest first, each with its `alert`.\n\n"
    "Amounts are text with 2 decimals and thousands commas, in `analysis.currency` (NGN). Nothing is changed or "
    "recorded by this call."
)


def paged(results):
    return {"count": len(results), "next": None, "previous": None, "results": results}


UNAUTHORIZED = OpenApiResponse(
    response=OpenApiTypes.OBJECT,
    description="No access token, or it is expired or invalid.",
    examples=[OpenApiExample("No token", value={"detail": "Authentication credentials were not provided."})],
)
FORBIDDEN = OpenApiResponse(
    response=OpenApiTypes.OBJECT,
    description="Temporary password not changed yet, or the account belongs to no organisation.",
    examples=[
        OpenApiExample("Temporary password", value={"error": PASSWORD_CHANGE_REQUIRED_MESSAGE, "code": "password_change_required"}),
        OpenApiExample("No organisation", value={"error": NO_ORGANISATION_MESSAGE}),
    ],
)
NOT_FOUND = OpenApiResponse(
    response=OpenApiTypes.OBJECT,
    description="No alert with this reference in your organisation.",
    examples=[OpenApiExample("Not found", value={"detail": "No alert with this reference."})],
)
REFERENCE_PARAM = OpenApiParameter(
    "reference", OpenApiTypes.STR, OpenApiParameter.PATH, description="The alert's reference, e.g. `ALT-0002` (any letter case)."
)


class AlertViewSet(viewsets.GenericViewSet):
    """Alerts screen and Alert detail screen."""

    permission_classes = [IsAuthenticated, PasswordChangeNotRequired, BelongsToOrganisation]
    serializer_class = AlertListSerializer
    queryset = Alert.objects.none()  # Only tells Swagger the model; requests use get_queryset().
    lookup_field = "reference"
    lookup_value_regex = "[^/]+"

    def get_queryset(self):
        # Scoped to the caller's organisation only, never an ID from the request.
        return Alert.objects.filter(organisation=self.request.user.organisation).select_related("assigned_reviewer", "upload", "case")

    def get_object(self):
        match = REFERENCE_PATTERN.fullmatch(self.kwargs["reference"])
        alert = self.get_queryset().filter(number=int(match.group(1))).first() if match else None
        if alert is None:
            raise Http404("No alert with this reference.")
        return alert

    def _detail(self, alert):
        return Response(AlertDetailSerializer(alert).data)

    @extend_schema(
        tags=["Alerts"],
        summary="List alerts",
        description=(
            "**Alerts screen.** The organisation's alerts, newest first.\n\n"
            "**Filters** (all optional, combined with AND):\n"
            "- `status`: `new`, `investigating`, `escalated`, `false_positive`\n"
            "- `severity`: `critical`, `high`, `medium`, `low`\n"
            "- `rule`: `duplicate_bank_account`, `duplicate_payment`\n"
            "- `department`: exact name, letter case ignored\n"
            "- `reviewer`: a user ID, or `none` for unassigned alerts\n"
            "- `search`: the search box; matches the entity name, entity ID, reference (`ALT-0002`), or risk type, "
            "letter case ignored\n\n"
            "An unknown `status`, `severity`, or `rule` value is 400.\n\n" + PAGINATION_NOTE + "\n\n" + AUTH_NOTE
        ),
        parameters=[
            OpenApiParameter("status", OpenApiTypes.STR, description="Filter by status."),
            OpenApiParameter("severity", OpenApiTypes.STR, description="Filter by severity."),
            OpenApiParameter("rule", OpenApiTypes.STR, description="Filter by rule."),
            OpenApiParameter("department", OpenApiTypes.STR, description="Filter by department."),
            OpenApiParameter("reviewer", OpenApiTypes.STR, description="Reviewer's user ID, or `none`."),
            OpenApiParameter("search", OpenApiTypes.STR, description="Search text."),
            OpenApiParameter("page", OpenApiTypes.INT, description="Page number, from 1."),
            OpenApiParameter("page_size", OpenApiTypes.INT, description="Items per page, default 20, at most 100."),
        ],
        responses={
            200: OpenApiResponse(response=AlertListSerializer(many=True), examples=[OpenApiExample("Alerts", value=paged([ALERT_EXAMPLE]))]),
            400: OpenApiResponse(response=OpenApiTypes.OBJECT, examples=[OpenApiExample("Unknown status", value={"status": ['"closed" is not a valid choice.']})]),
            401: UNAUTHORIZED,
            403: FORBIDDEN,
            404: OpenApiResponse(response=OpenApiTypes.OBJECT, examples=[OpenApiExample("Page past the end", value={"detail": "Invalid page."})]),
        },
    )
    def list(self, request):
        filters = AlertListFilterSerializer(data=request.query_params)
        filters.is_valid(raise_exception=True)
        f = filters.validated_data
        alerts = self.get_queryset()
        for name in ("status", "severity", "rule"):
            if name in f:
                alerts = alerts.filter(**{name: f[name]})
        if "department" in f:
            alerts = alerts.filter(department__iexact=f["department"])
        if "reviewer" in f:
            alerts = alerts.filter(assigned_reviewer__isnull=True) if f["reviewer"] == "none" else alerts.filter(assigned_reviewer_id=f["reviewer"])
        if f.get("search"):
            text = f["search"].strip()
            match = Q(entity_name__icontains=text) | Q(entity_ref__icontains=text) | Q(risk_type__icontains=text) | Q(title__icontains=text)
            number = REFERENCE_PATTERN.fullmatch(text)
            if number:
                match |= Q(number=int(number.group(1)))
            alerts = alerts.filter(match)
        page = self.paginate_queryset(alerts)
        return self.get_paginated_response(AlertListSerializer(page, many=True).data)

    @extend_schema(
        tags=["Alerts"],
        summary="View one alert",
        description=(
            "**Alert detail screen.** Everything about one alert: the explanation (\"Why This Was Flagged\"), "
            "flagged records, event timeline, assigned reviewer, the upload that triggered it, and reserved AI "
            "fields (empty until ML fills them). `evidence` lists the files attached to the alert's case (`[]` when it is in no case), each with a 1-hour download link. Amounts in "
            "`flagged_records` are text with 2 decimals in `currency` (NGN).\n\n" + AUTH_NOTE
        ),
        parameters=[REFERENCE_PARAM],
        responses={200: OpenApiResponse(response=AlertDetailSerializer, examples=[OpenApiExample("Alert", value=DETAIL_EXAMPLE)]), 401: UNAUTHORIZED, 403: FORBIDDEN, 404: NOT_FOUND},
    )
    def retrieve(self, request, reference=None):
        return self._detail(self.get_object())

    @extend_schema(
        tags=["Alerts"],
        summary="Investigate an alert",
        description=(
            "**Investigation drill-down from the Alert detail screen.** Read-only: who and what the alert is "
            "connected to, so an analyst can judge it before converting it to a case. For a case covering several "
            "alerts use `GET /api/v1/fraud/cases/{reference}/investigation/` instead.\n\n"
            + INVESTIGATION_NOTE + "\n\n" + AUTH_NOTE
        ),
        parameters=[REFERENCE_PARAM],
        responses={
            200: OpenApiResponse(response=InvestigationSerializer, examples=[OpenApiExample("Investigation", value=INVESTIGATION_EXAMPLE)]),
            401: UNAUTHORIZED, 403: FORBIDDEN, 404: NOT_FOUND,
        },
    )
    @action(detail=True, methods=["get"])
    def investigation(self, request, reference=None):
        alert = self.get_object()
        result = InvestigationService.build(request.user.organisation, [alert], {"type": "alert", "reference": alert.reference})
        return Response(InvestigationSerializer(result).data, status=status.HTTP_200_OK)

    @extend_schema(
        tags=["Alerts"],
        summary="Change an alert's status",
        description=(
            "Moves the alert to `new`, `investigating`, `escalated`, or `false_positive`. **Mark False Positive** "
            "sends `false_positive`; reopening sends `new` or `investigating`. Any change between these four is "
            "allowed; sending the current status is 400. An optional `note` (up to 2000 characters) is saved in the "
            "history. 200 returns the updated alert. Every change is recorded in the alert's history and the audit "
            "log.\n\n" + AUTH_NOTE
        ),
        parameters=[REFERENCE_PARAM],
        request=StatusChangeSerializer,
        examples=[OpenApiExample("Mark false positive", value={"status": "false_positive", "note": "Both payments were refunded by the vendor."}, request_only=True)],
        responses={
            200: OpenApiResponse(response=AlertDetailSerializer, examples=[OpenApiExample("Updated", value={**DETAIL_EXAMPLE, "status": "false_positive"})]),
            400: OpenApiResponse(
                response=OpenApiTypes.OBJECT,
                examples=[
                    OpenApiExample("Same status", value={"status": ["The alert is already New."]}),
                    OpenApiExample("Unknown status", value={"status": ['"closed" is not a valid choice.']}),
                ],
            ),
            401: UNAUTHORIZED, 403: FORBIDDEN, 404: NOT_FOUND,
        },
    )
    @action(detail=True, methods=["post"], url_path="status")
    def change_status(self, request, reference=None):
        alert = self.get_object()
        serializer = StatusChangeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        new_status, note = serializer.validated_data["status"], serializer.validated_data.get("note", "").strip()
        if new_status == alert.status:
            raise serializers.ValidationError({"status": [f"The alert is already {alert.get_status_display()}."]})
        old_status = alert.status
        with transaction.atomic():
            alert.status = new_status
            alert.save(update_fields=["status", "updated_at"])
            AlertEvent.objects.create(alert=alert, type=AlertEvent.STATUS_CHANGED, actor=request.user, from_status=old_status, to_status=new_status, detail=note)
            AuditLog.record(
                AuditLog.ALERT_STATUS_CHANGED, actor=request.user, target=alert, request=request,
                details={"reference": alert.reference, "from": old_status, "to": new_status},
            )
        return self._detail(alert)

    @extend_schema(
        tags=["Alerts"],
        summary="Alert history",
        description=(
            "Everything that happened to the alert, **oldest first**: created by detection (`actor: null`), status "
            "changes (with `from_status`, `to_status`, and the note in `detail`), reviewer assigned or removed, and "
            "notes added.\n\n" + PAGINATION_NOTE + "\n\n" + AUTH_NOTE
        ),
        parameters=[REFERENCE_PARAM],
        responses={
            200: OpenApiResponse(
                response=AlertEventSerializer(many=True),
                examples=[OpenApiExample("History", value=paged([
                    {"id": "0f9e6a1c-7b2d-4c3e-9a8f-1b2c3d4e5f60", "type": "created", "actor": None, "from_status": "", "to_status": "new", "detail": "Invoice INV-2024-8847 paid 2 times to Acme Supplies", "created_at": "2026-10-14T18:02:11Z"},
                    {"id": "1a2b3c4d-5e6f-4a1b-8c2d-3e4f5a6b7c8d", "type": "status_changed", "actor": {"id": "2d089ad3-9545-4611-a9dc-731c3a81879f", "full_name": "Ngozi Okafor"}, "from_status": "new", "to_status": "investigating", "detail": "", "created_at": "2026-10-14T18:10:00Z"},
                ]))],
            ),
            401: UNAUTHORIZED, 403: FORBIDDEN, 404: NOT_FOUND,
        },
    )
    @action(detail=True, methods=["get"])
    def history(self, request, reference=None):
        page = self.paginate_queryset(self.get_object().events.select_related("actor").order_by("created_at"))
        return self.get_paginated_response(AlertEventSerializer(page, many=True).data)

    @extend_schema(
        tags=["Alerts"],
        summary="Assign or remove the alert's reviewer",
        description=(
            "**Assignment And Notes box.** Sets the reviewer to an active member of your organisation (super admin "
            "or team member), or removes it with `null`. A user from another organisation, a deactivated user, or an "
            "unknown ID is 400. 200 returns the updated alert. Recorded in the history and audit log.\n\n" + AUTH_NOTE
        ),
        parameters=[REFERENCE_PARAM],
        request=ReviewerSerializer,
        examples=[
            OpenApiExample("Assign", value={"reviewer_id": "7c1e2f4a-9b3d-4e6f-8a2c-1d5b7e9f0a3c"}, request_only=True),
            OpenApiExample("Remove", value={"reviewer_id": None}, request_only=True),
        ],
        responses={
            200: OpenApiResponse(response=AlertDetailSerializer, examples=[OpenApiExample("Assigned", value={**DETAIL_EXAMPLE, "assigned_reviewer": {"id": "7c1e2f4a-9b3d-4e6f-8a2c-1d5b7e9f0a3c", "full_name": "Ada Obi", "email": "ada.obi@ikejacoop.ng"}})]),
            400: OpenApiResponse(response=OpenApiTypes.OBJECT, examples=[OpenApiExample("Not a member", value={"reviewer_id": [REVIEWER_INVALID_MESSAGE]})]),
            401: UNAUTHORIZED, 403: FORBIDDEN, 404: NOT_FOUND,
        },
    )
    @action(detail=True, methods=["put"])
    def reviewer(self, request, reference=None):
        alert = self.get_object()
        serializer = ReviewerSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        reviewer_id = serializer.validated_data["reviewer_id"]
        reviewer = None
        if reviewer_id is not None:
            reviewer = User.objects.filter(pk=reviewer_id, organisation=request.user.organisation, is_active=True).first()
            if reviewer is None:
                raise serializers.ValidationError({"reviewer_id": [REVIEWER_INVALID_MESSAGE]})
        with transaction.atomic():
            alert.assigned_reviewer = reviewer
            alert.save(update_fields=["assigned_reviewer", "updated_at"])
            AlertEvent.objects.create(
                alert=alert,
                type=AlertEvent.REVIEWER_ASSIGNED if reviewer else AlertEvent.REVIEWER_REMOVED,
                actor=request.user,
                detail=reviewer.full_name if reviewer else "",
            )
            AuditLog.record(
                AuditLog.ALERT_REVIEWER_CHANGED, actor=request.user, target=alert, request=request,
                details={"reference": alert.reference, "reviewer_id": str(reviewer.id) if reviewer else None},
            )
        return self._detail(alert)

    @extend_schema(
        tags=["Alerts"],
        summary="List an alert's notes",
        description="The notes on the alert, newest first, such as the \"Instruction to Reviewer\".\n\n" + PAGINATION_NOTE + "\n\n" + AUTH_NOTE,
        parameters=[REFERENCE_PARAM],
        methods=["GET"],
        responses={
            200: OpenApiResponse(response=AlertNoteSerializer(many=True), examples=[OpenApiExample("Notes", value=paged([{"id": "5b6c7d8e-9f0a-4b1c-8d2e-3f4a5b6c7d8e", "text": "Check both approvals with John Doe before escalating.", "author": {"id": "2d089ad3-9545-4611-a9dc-731c3a81879f", "full_name": "Ngozi Okafor"}, "created_at": "2026-10-14T18:12:00Z"}]))]),
            401: UNAUTHORIZED, 403: FORBIDDEN, 404: NOT_FOUND,
        },
    )
    @extend_schema(
        tags=["Alerts"],
        summary="Add a note to an alert",
        description=(
            "**Save Note button.** Adds a note (1 to 2000 characters, spaces trimmed), for example the instruction "
            "to the reviewer. Notes cannot be edited or deleted. 201 returns the note. Recorded in the history and "
            "audit log.\n\n" + AUTH_NOTE
        ),
        parameters=[REFERENCE_PARAM],
        methods=["POST"],
        request=AlertNoteSerializer,
        examples=[OpenApiExample("Instruction", value={"text": "Check both approvals with John Doe before escalating."}, request_only=True)],
        responses={
            201: OpenApiResponse(response=AlertNoteSerializer, examples=[OpenApiExample("Created", value={"id": "5b6c7d8e-9f0a-4b1c-8d2e-3f4a5b6c7d8e", "text": "Check both approvals with John Doe before escalating.", "author": {"id": "2d089ad3-9545-4611-a9dc-731c3a81879f", "full_name": "Ngozi Okafor"}, "created_at": "2026-10-14T18:12:00Z"})]),
            400: OpenApiResponse(response=OpenApiTypes.OBJECT, examples=[OpenApiExample("Empty", value={"text": ["This field may not be blank."]}), OpenApiExample("Too long", value={"text": ["Ensure this field has no more than 2000 characters."]})]),
            401: UNAUTHORIZED, 403: FORBIDDEN, 404: NOT_FOUND,
        },
    )
    @action(detail=True, methods=["get", "post"])
    def notes(self, request, reference=None):
        alert = self.get_object()
        if request.method == "GET":
            page = self.paginate_queryset(alert.notes.select_related("author"))
            return self.get_paginated_response(AlertNoteSerializer(page, many=True).data)
        serializer = AlertNoteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            note = AlertNote.objects.create(alert=alert, author=request.user, text=serializer.validated_data["text"])
            AlertEvent.objects.create(alert=alert, type=AlertEvent.NOTE_ADDED, actor=request.user, detail=note.text)
            AuditLog.record(
                AuditLog.ALERT_NOTE_ADDED, actor=request.user, target=alert, request=request,
                details={"reference": alert.reference},
            )
        return Response(AlertNoteSerializer(note).data, status=status.HTTP_201_CREATED)
