import re

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Count, Max, Q
from django.http import Http404
from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from accounts.models import AuditLog, Organisation
from accounts.permissions import PasswordChangeNotRequired
from ingestion.viewsets import BelongsToOrganisation

from .case_serializers import (
    CaseAssignmentSerializer,
    CaseCommentSerializer,
    CaseCreateSerializer,
    CaseDetailSerializer,
    CaseEscalateSerializer,
    CaseEventSerializer,
    CaseListFilterSerializer,
    CaseListSerializer,
    CaseStatusSerializer,
)
from .models import Alert, AlertEvent, Case, CaseComment, CaseEvent
from .viewsets import ALERT_EXAMPLE, FORBIDDEN, PAGINATION_NOTE, REFERENCE_PATTERN, REVIEWER_INVALID_MESSAGE, UNAUTHORIZED, paged

User = get_user_model()
CASE_REFERENCE_PATTERN = re.compile(r"CASE-(\d{4,})", re.IGNORECASE)
SEVERITY_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3}
ESCALATE_INSTEAD_MESSAGE = "Use the escalate endpoint to escalate a case."
CLOSED_CASE_MESSAGE = "A closed case cannot be escalated. Reopen it first."

AUTH_NOTE = (
    "**Authentication:** `Authorization: Bearer <access_token>` of any member of the organisation (super admin "
    "or team member). Only the caller's own organisation's cases exist for them; any other reference is 404."
)
SLA_NOTE = (
    "**SLA:** due 2 (critical), 5 (high), 7 (medium), or 14 (low) days after the case was opened. "
    "`sla_days_remaining` is rounded up (\"2 days remaining\"), 0 or negative when overdue, `null` once closed; "
    "changing the priority (escalation) moves the due date."
)
PERSON = {"id": "7c1e2f4a-9b3d-4e6f-8a2c-1d5b7e9f0a3c", "full_name": "Ada Obi", "email": "ada.obi@ikejacoop.ng"}
ACTOR = {"id": "2d089ad3-9545-4611-a9dc-731c3a81879f", "full_name": "Ngozi Okafor"}
CASE_EXAMPLE = {
    "reference": "CASE-0001",
    "title": "Invoice INV-2024-8847 paid 2 times to Acme Supplies",
    "status": "new",
    "priority": "high",
    "investigator": PERSON,
    "alerts_count": 1,
    "sla_due_at": "2026-10-20T09:00:00Z",
    "sla_days_remaining": 5,
    "days_open": 0,
    "created_at": "2026-10-15T09:00:00Z",
    "updated_at": "2026-10-15T09:00:00Z",
}
DETAIL_EXAMPLE = {
    **CASE_EXAMPLE,
    "description": "Check both approvals with John Doe.",
    "reviewer": None,
    "created_by": ACTOR,
    "escalation": None,
    "alerts": [{**ALERT_EXAMPLE, "status": "investigating"}],
    "related": {"employees": [], "vendors": [{"id": "VND-8847", "name": "Acme Supplies"}], "approvers": ["John Doe"]},
    "transactions": [
        {"transaction_id": "TXN-90214", "payment_date": "2026-05-05", "amount": "42,350.00", "invoice_number": "INV-2024-8847", "approver": "John Doe", "alert": "ALT-0002"},
        {"transaction_id": "TXN-90455", "payment_date": "2026-05-08", "amount": "42,350.00", "invoice_number": "INV-2024-8847", "approver": "John Doe", "alert": "ALT-0002"},
    ],
    "comments_count": 0,
    "closed_at": None,
    "currency": "NGN",
    "ai_summary": "",
    "ai_recommendations": [],
}
ESCALATED_EXAMPLE = {
    **DETAIL_EXAMPLE,
    "status": "escalated",
    "priority": "critical",
    "sla_due_at": "2026-10-17T09:00:00Z",
    "sla_days_remaining": 2,
    "escalation": {
        "reason": "financial_threshold",
        "reason_label": "Exceeds financial approval limit",
        "escalated_to": {"id": "9a8b7c6d-5e4f-4a3b-9c2d-1e0f9a8b7c6d", "full_name": "Tunde Bello", "email": "tunde.bello@ikejacoop.ng"},
        "notes": "Above the 1,000,000 NGN approval limit.",
        "escalated_at": "2026-10-15T11:30:00Z",
    },
}
COMMENT_EXAMPLE = {"id": "5b6c7d8e-9f0a-4b1c-8d2e-3f4a5b6c7d8e", "text": "Bank statement requested from GTBank.", "author": ACTOR, "created_at": "2026-10-15T10:00:00Z"}

NOT_FOUND = OpenApiResponse(
    response=OpenApiTypes.OBJECT,
    description="No case with this reference in your organisation.",
    examples=[OpenApiExample("Not found", value={"detail": "No case with this reference."})],
)
REFERENCE_PARAM = OpenApiParameter(
    "reference", OpenApiTypes.STR, OpenApiParameter.PATH, description="The case's reference, e.g. `CASE-0001` (any letter case)."
)
NOT_A_MEMBER = OpenApiExample("Not a member", value={"investigator_id": [REVIEWER_INVALID_MESSAGE]})


def _member(organisation, user_id):
    return User.objects.filter(pk=user_id, organisation=organisation, is_active=True).first()


class CaseViewSet(viewsets.GenericViewSet):
    """Investigations screen, Case detail screen, and Escalate screen."""

    permission_classes = [IsAuthenticated, PasswordChangeNotRequired, BelongsToOrganisation]
    serializer_class = CaseListSerializer
    queryset = Case.objects.none()  # Only tells Swagger the model; requests use get_queryset().
    lookup_field = "reference"
    lookup_value_regex = "[^/]+"

    def get_queryset(self):
        # Scoped to the caller's organisation only, never an ID from the request.
        return (
            Case.objects.filter(organisation=self.request.user.organisation)
            .select_related("investigator", "reviewer", "created_by", "escalated_to")
            .annotate(alerts_count=Count("alerts"))
            .order_by("-created_at", "-number")  # GROUP BY drops the model ordering.
        )

    def get_object(self):
        match = CASE_REFERENCE_PATTERN.fullmatch(self.kwargs["reference"])
        case = self.get_queryset().filter(number=int(match.group(1))).first() if match else None
        if case is None:
            raise Http404("No case with this reference.")
        return case

    def _detail(self, case, code=status.HTTP_200_OK):
        return Response(CaseDetailSerializer(case).data, status=code)

    @extend_schema(
        tags=["Cases"],
        summary="List cases",
        description=(
            "**Investigations screen.** The organisation's cases, newest first, with the columns of the table "
            "(reference, title, status, assigned investigator, priority, created, last updated, SLA).\n\n"
            "**Filters** (all optional, combined with AND):\n"
            "- `status`: `new`, `investigating`, `under_review`, `escalated`, `closed`\n"
            "- `priority`: `critical`, `high`, `medium`, `low`\n"
            "- `investigator`: a user ID, or `none` for cases nobody investigates yet\n"
            "- `search`: matches the case reference (`CASE-0001`), the title, a linked alert's reference "
            "(`ALT-0002`), or a linked alert's employee or vendor name, letter case ignored\n\n"
            "An unknown `status` or `priority` is 400.\n\n" + SLA_NOTE + "\n\n" + PAGINATION_NOTE + "\n\n" + AUTH_NOTE
        ),
        parameters=[
            OpenApiParameter("status", OpenApiTypes.STR, description="Filter by status."),
            OpenApiParameter("priority", OpenApiTypes.STR, description="Filter by priority."),
            OpenApiParameter("investigator", OpenApiTypes.STR, description="Investigator's user ID, or `none`."),
            OpenApiParameter("search", OpenApiTypes.STR, description="Search text."),
            OpenApiParameter("page", OpenApiTypes.INT, description="Page number, from 1."),
            OpenApiParameter("page_size", OpenApiTypes.INT, description="Items per page, default 20, at most 100."),
        ],
        responses={
            200: OpenApiResponse(response=CaseListSerializer(many=True), examples=[OpenApiExample("Cases", value=paged([CASE_EXAMPLE]))]),
            400: OpenApiResponse(response=OpenApiTypes.OBJECT, examples=[OpenApiExample("Unknown status", value={"status": ['"open" is not a valid choice.']})]),
            401: UNAUTHORIZED,
            403: FORBIDDEN,
            404: OpenApiResponse(response=OpenApiTypes.OBJECT, examples=[OpenApiExample("Page past the end", value={"detail": "Invalid page."})]),
        },
    )
    def list(self, request):
        filters = CaseListFilterSerializer(data=request.query_params)
        filters.is_valid(raise_exception=True)
        f = filters.validated_data
        cases = self.get_queryset()
        for name in ("status", "priority"):
            if name in f:
                cases = cases.filter(**{name: f[name]})
        if "investigator" in f:
            cases = cases.filter(investigator__isnull=True) if f["investigator"] == "none" else cases.filter(investigator_id=f["investigator"])
        if f.get("search"):
            text = f["search"].strip()
            alert_match = Q(entity_name__icontains=text)
            alert_number = REFERENCE_PATTERN.fullmatch(text)
            if alert_number:
                alert_match |= Q(number=int(alert_number.group(1)))
            # A subquery, so the alerts join does not inflate alerts_count.
            match = Q(title__icontains=text) | Q(pk__in=Alert.objects.filter(alert_match, case__isnull=False).values("case_id"))
            case_number = CASE_REFERENCE_PATTERN.fullmatch(text)
            if case_number:
                match |= Q(number=int(case_number.group(1)))
            cases = cases.filter(match)
        page = self.paginate_queryset(cases)
        return self.get_paginated_response(CaseListSerializer(page, many=True).data)

    @extend_schema(
        tags=["Cases"],
        summary="Convert alerts to a case",
        description=(
            "**Convert to Case button** (Alert detail screen). Opens a case for one or more alerts of your "
            "organisation and returns it (201). The server gives it the next reference for your organisation "
            "(`CASE-0001`, `CASE-0002`, ...), status `new`, and the priority of its most severe alert.\n\n"
            "**Fields:**\n"
            "- `alerts` (required): 1 to 20 alert references such as `ALT-0002`; repeats are ignored\n"
            "- `title` (optional, up to 255): defaults to the first alert's title, plus `(+N more alerts)`\n"
            "- `description` (optional, up to 2000)\n"
            "- `investigator_id` (optional): an active member of your organisation\n\n"
            "**What else happens:** every linked alert that is `new` moves to `investigating`, with an entry in its "
            "history (`Converted to CASE-0001`). The alert detail then shows the case in its `case` field. The "
            "case's history starts with `created`, and the audit log records it.\n\n"
            "**400** when a reference is not one of your alerts, an alert is already in a case (one case per "
            "alert), an alert is marked false positive (change its status first), or the investigator is not an "
            "active member. Nothing is saved on a 400.\n\n"
            "**Next for the frontend:** open the case page at `/investigations/{reference}`.\n\n" + AUTH_NOTE
        ),
        request=CaseCreateSerializer,
        examples=[
            OpenApiExample("One alert", value={"alerts": ["ALT-0002"]}, request_only=True),
            OpenApiExample(
                "Several alerts, with title and investigator",
                value={"alerts": ["ALT-0002", "ALT-0005"], "title": "Acme Supplies duplicate invoices", "description": "Check both approvals with John Doe.", "investigator_id": PERSON["id"]},
                request_only=True,
            ),
        ],
        responses={
            201: OpenApiResponse(response=CaseDetailSerializer, examples=[OpenApiExample("Created", value=DETAIL_EXAMPLE)]),
            400: OpenApiResponse(
                response=OpenApiTypes.OBJECT,
                examples=[
                    OpenApiExample("Unknown alert", value={"alerts": ["No alert ALT-0099 in your organisation."]}),
                    OpenApiExample("Already in a case", value={"alerts": ["ALT-0002 is already in CASE-0001."]}),
                    OpenApiExample("False positive", value={"alerts": ["ALT-0003 is marked false positive; change its status first."]}),
                    OpenApiExample("Empty list", value={"alerts": ["Ensure this field has at least 1 elements."]}),
                    NOT_A_MEMBER,
                ],
            ),
            401: UNAUTHORIZED,
            403: FORBIDDEN,
        },
    )
    def create(self, request):
        serializer = CaseCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        organisation = request.user.organisation
        numbers, unknown = [], []
        for raw in data["alerts"]:
            match = REFERENCE_PATTERN.fullmatch(raw.strip())
            if not match:
                unknown.append(raw.strip())
            elif int(match.group(1)) not in numbers:
                numbers.append(int(match.group(1)))
        investigator = None
        if data.get("investigator_id"):
            investigator = _member(organisation, data["investigator_id"])
            if investigator is None:
                raise serializers.ValidationError({"investigator_id": [REVIEWER_INVALID_MESSAGE]})

        with transaction.atomic():
            # Locking the organisation row gives concurrent requests case numbers one after another,
            # and stops two requests putting the same alert into two cases.
            Organisation.objects.select_for_update().get(pk=organisation.pk)
            found = {a.number: a for a in Alert.objects.filter(organisation=organisation, number__in=numbers)}
            unknown += [f"ALT-{n:04d}" for n in numbers if n not in found]
            if unknown:
                raise serializers.ValidationError({"alerts": [f"No alert {', '.join(unknown)} in your organisation."]})
            alerts = [found[n] for n in numbers]
            problems = []
            for alert in alerts:
                if alert.case_id:
                    problems.append(f"{alert.reference} is already in {alert.case.reference}.")
                elif alert.status == "false_positive":
                    problems.append(f"{alert.reference} is marked false positive; change its status first.")
            if problems:
                raise serializers.ValidationError({"alerts": problems})

            title = data.get("title", "").strip() or alerts[0].title[:230] + (f" (+{len(alerts) - 1} more alerts)" if len(alerts) > 1 else "")
            number = (Case.objects.filter(organisation=organisation).aggregate(top=Max("number"))["top"] or 0) + 1
            case = Case.objects.create(
                organisation=organisation, number=number, title=title, description=data.get("description", "").strip(),
                priority=min((a.severity for a in alerts), key=SEVERITY_RANK.__getitem__),
                investigator=investigator, created_by=request.user,
            )
            CaseEvent.objects.create(case=case, type=CaseEvent.CREATED, actor=request.user, to_status=case.status, detail=case.title)
            if investigator:
                CaseEvent.objects.create(case=case, type=CaseEvent.INVESTIGATOR_ASSIGNED, actor=request.user, detail=investigator.full_name)
            for alert in alerts:
                alert.case = case
                old_status = alert.status
                if old_status == "new":
                    alert.status = "investigating"
                alert.save(update_fields=["case", "status", "updated_at"])
                if old_status != alert.status:
                    AlertEvent.objects.create(
                        alert=alert, type=AlertEvent.STATUS_CHANGED, actor=request.user, from_status=old_status,
                        to_status=alert.status, detail=f"Converted to {case.reference}",
                    )
                    AuditLog.record(
                        AuditLog.ALERT_STATUS_CHANGED, actor=request.user, target=alert, request=request,
                        details={"reference": alert.reference, "from": old_status, "to": alert.status, "case": case.reference},
                    )
            AuditLog.record(
                AuditLog.CASE_CREATED, actor=request.user, target=case, request=request,
                details={"reference": case.reference, "alerts": [a.reference for a in alerts]},
            )
        return self._detail(self.get_queryset().get(pk=case.pk), status.HTTP_201_CREATED)

    @extend_schema(
        tags=["Cases"],
        summary="View one case",
        description=(
            "**Case detail screen.** Everything about one case: status, priority, investigator, reviewer, SLA, days "
            "open, the linked alerts (Alerts-table shape), related entities (employees, vendors, approvers taken "
            "from the linked alerts), connected transactions (the linked alerts' flagged payments, each with its "
            "`alert` reference), escalation details, comment count, and reserved AI fields (empty until ML). "
            "The timeline and status history come from `GET .../history/`; comments from `GET .../comments/`. "
            "Evidence files are added by a later feature.\n\n" + SLA_NOTE + "\n\n" + AUTH_NOTE
        ),
        parameters=[REFERENCE_PARAM],
        responses={200: OpenApiResponse(response=CaseDetailSerializer, examples=[OpenApiExample("Case", value=DETAIL_EXAMPLE)]), 401: UNAUTHORIZED, 403: FORBIDDEN, 404: NOT_FOUND},
    )
    def retrieve(self, request, reference=None):
        return self._detail(self.get_object())

    @extend_schema(
        tags=["Cases"],
        summary="Change a case's status",
        description=(
            "**Quick actions:** Start Investigation (`investigating`), send for review (`under_review`), Close Case "
            "(`closed`), Re-Open Investigation (`investigating` from `closed`). Any change between `new`, "
            "`investigating`, `under_review`, and `closed` is allowed. `escalated` is only set by "
            "`POST .../escalate/`, because it needs a reason and a person (400 here). Sending the current status "
            "is 400.\n\n"
            "Closing sets `closed_at` and stops the SLA (`sla_days_remaining: null`); reopening clears `closed_at`. "
            "An optional `note` (up to 2000 characters) is saved in the history. 200 returns the updated case. "
            "Every change is recorded in the case history and the audit log.\n\n" + AUTH_NOTE
        ),
        parameters=[REFERENCE_PARAM],
        request=CaseStatusSerializer,
        examples=[
            OpenApiExample("Start investigation", value={"status": "investigating"}, request_only=True),
            OpenApiExample("Close", value={"status": "closed", "note": "Vendor refunded the duplicate payment."}, request_only=True),
        ],
        responses={
            200: OpenApiResponse(response=CaseDetailSerializer, examples=[OpenApiExample("Updated", value={**DETAIL_EXAMPLE, "status": "investigating"})]),
            400: OpenApiResponse(
                response=OpenApiTypes.OBJECT,
                examples=[
                    OpenApiExample("Same status", value={"status": ["The case is already New."]}),
                    OpenApiExample("Escalate here", value={"status": [ESCALATE_INSTEAD_MESSAGE]}),
                    OpenApiExample("Unknown status", value={"status": ['"open" is not a valid choice.']}),
                ],
            ),
            401: UNAUTHORIZED, 403: FORBIDDEN, 404: NOT_FOUND,
        },
    )
    @action(detail=True, methods=["post"], url_path="status")
    def change_status(self, request, reference=None):
        case = self.get_object()
        serializer = CaseStatusSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        new_status, note = serializer.validated_data["status"], serializer.validated_data.get("note", "").strip()
        if new_status == "escalated":
            raise serializers.ValidationError({"status": [ESCALATE_INSTEAD_MESSAGE]})
        if new_status == case.status:
            raise serializers.ValidationError({"status": [f"The case is already {case.get_status_display()}."]})
        old_status = case.status
        with transaction.atomic():
            case.status = new_status
            case.closed_at = timezone.now() if new_status == "closed" else None
            case.save(update_fields=["status", "closed_at", "updated_at"])
            CaseEvent.objects.create(case=case, type=CaseEvent.STATUS_CHANGED, actor=request.user, from_status=old_status, to_status=new_status, detail=note)
            AuditLog.record(
                AuditLog.CASE_STATUS_CHANGED, actor=request.user, target=case, request=request,
                details={"reference": case.reference, "from": old_status, "to": new_status},
            )
        return self._detail(case)

    @extend_schema(
        tags=["Cases"],
        summary="Assign the investigator and reviewer",
        description=(
            "**Assign Reviewer quick action and the Investigator box.** Send `investigator_id`, `reviewer_id`, or "
            "both. Each is the ID of an active member of your organisation (super admin or team member), or `null` "
            "to remove that person. A field left out stays as it is; an empty body is 400. A user from another "
            "organisation, a deactivated user, or an unknown ID is 400 and nothing changes.\n\n"
            "200 returns the updated case. Each actual change is recorded in the history "
            "(`investigator_assigned`, `investigator_removed`, `reviewer_assigned`, `reviewer_removed`) and in the "
            "audit log.\n\n" + AUTH_NOTE
        ),
        parameters=[REFERENCE_PARAM],
        request=CaseAssignmentSerializer,
        examples=[
            OpenApiExample("Assign both", value={"investigator_id": PERSON["id"], "reviewer_id": "9a8b7c6d-5e4f-4a3b-9c2d-1e0f9a8b7c6d"}, request_only=True),
            OpenApiExample("Remove reviewer", value={"reviewer_id": None}, request_only=True),
        ],
        responses={
            200: OpenApiResponse(response=CaseDetailSerializer, examples=[OpenApiExample("Assigned", value=DETAIL_EXAMPLE)]),
            400: OpenApiResponse(
                response=OpenApiTypes.OBJECT,
                examples=[NOT_A_MEMBER, OpenApiExample("Empty body", value={"non_field_errors": ["Send `investigator_id`, `reviewer_id`, or both."]})],
            ),
            401: UNAUTHORIZED, 403: FORBIDDEN, 404: NOT_FOUND,
        },
    )
    @action(detail=True, methods=["put"])
    def assignment(self, request, reference=None):
        case = self.get_object()
        serializer = CaseAssignmentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        chosen = {}
        for role in ("investigator", "reviewer"):
            if f"{role}_id" not in serializer.validated_data:
                continue
            user_id = serializer.validated_data[f"{role}_id"]
            user = None
            if user_id is not None:
                user = _member(request.user.organisation, user_id)
                if user is None:
                    raise serializers.ValidationError({f"{role}_id": [REVIEWER_INVALID_MESSAGE]})
            chosen[role] = user
        events = {
            "investigator": (CaseEvent.INVESTIGATOR_ASSIGNED, CaseEvent.INVESTIGATOR_REMOVED),
            "reviewer": (CaseEvent.REVIEWER_ASSIGNED, CaseEvent.REVIEWER_REMOVED),
        }
        with transaction.atomic():
            changed = {}
            for role, user in chosen.items():
                if getattr(case, f"{role}_id") == (user.pk if user else None):
                    continue
                setattr(case, role, user)
                changed[f"{role}_id"] = str(user.pk) if user else None
                assigned, removed = events[role]
                CaseEvent.objects.create(case=case, type=assigned if user else removed, actor=request.user, detail=user.full_name if user else "")
            if changed:
                case.save(update_fields=[*(name[:-3] for name in changed), "updated_at"])
                AuditLog.record(AuditLog.CASE_ASSIGNED, actor=request.user, target=case, request=request, details={"reference": case.reference, **changed})
        return self._detail(case)

    @extend_schema(
        tags=["Cases"],
        summary="Case history",
        description=(
            "The case's timeline and status history, **oldest first**: `created`, `status_changed` (with "
            "`from_status`, `to_status`, and the note in `detail`), `investigator_assigned` / `investigator_removed`, "
            "`reviewer_assigned` / `reviewer_removed` (the person's name in `detail`), `comment_added` (the comment "
            "in `detail`), and `escalated` (from status, reason, and person in `detail`). For the Status History "
            "box, show the `status_changed` and `escalated` entries.\n\n" + PAGINATION_NOTE + "\n\n" + AUTH_NOTE
        ),
        parameters=[REFERENCE_PARAM],
        responses={
            200: OpenApiResponse(
                response=CaseEventSerializer(many=True),
                examples=[OpenApiExample("History", value=paged([
                    {"id": "0f9e6a1c-7b2d-4c3e-9a8f-1b2c3d4e5f60", "type": "created", "actor": ACTOR, "from_status": "", "to_status": "new", "detail": CASE_EXAMPLE["title"], "created_at": "2026-10-15T09:00:00Z"},
                    {"id": "1a2b3c4d-5e6f-4a1b-8c2d-3e4f5a6b7c8d", "type": "status_changed", "actor": ACTOR, "from_status": "new", "to_status": "investigating", "detail": "", "created_at": "2026-10-15T09:05:00Z"},
                ]))],
            ),
            401: UNAUTHORIZED, 403: FORBIDDEN, 404: NOT_FOUND,
        },
    )
    @action(detail=True, methods=["get"])
    def history(self, request, reference=None):
        page = self.paginate_queryset(self.get_object().events.select_related("actor").order_by("created_at"))
        return self.get_paginated_response(CaseEventSerializer(page, many=True).data)

    @extend_schema(
        tags=["Cases"],
        summary="List a case's comments",
        description="The Comments box: the case's comments, newest first.\n\n" + PAGINATION_NOTE + "\n\n" + AUTH_NOTE,
        parameters=[REFERENCE_PARAM],
        methods=["GET"],
        responses={
            200: OpenApiResponse(response=CaseCommentSerializer(many=True), examples=[OpenApiExample("Comments", value=paged([COMMENT_EXAMPLE]))]),
            401: UNAUTHORIZED, 403: FORBIDDEN, 404: NOT_FOUND,
        },
    )
    @extend_schema(
        tags=["Cases"],
        summary="Comment on a case",
        description=(
            "Adds a comment (1 to 2000 characters, spaces trimmed). Comments cannot be edited or deleted. Any member "
            "of the organisation can comment, on a case in any status. 201 returns the comment. Recorded in the "
            "history (`comment_added`) and the audit log.\n\n" + AUTH_NOTE
        ),
        parameters=[REFERENCE_PARAM],
        methods=["POST"],
        request=CaseCommentSerializer,
        examples=[OpenApiExample("Comment", value={"text": "Bank statement requested from GTBank."}, request_only=True)],
        responses={
            201: OpenApiResponse(response=CaseCommentSerializer, examples=[OpenApiExample("Created", value=COMMENT_EXAMPLE)]),
            400: OpenApiResponse(response=OpenApiTypes.OBJECT, examples=[OpenApiExample("Empty", value={"text": ["This field may not be blank."]}), OpenApiExample("Too long", value={"text": ["Ensure this field has no more than 2000 characters."]})]),
            401: UNAUTHORIZED, 403: FORBIDDEN, 404: NOT_FOUND,
        },
    )
    @action(detail=True, methods=["get", "post"])
    def comments(self, request, reference=None):
        case = self.get_object()
        if request.method == "GET":
            page = self.paginate_queryset(case.comments.select_related("author"))
            return self.get_paginated_response(CaseCommentSerializer(page, many=True).data)
        serializer = CaseCommentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            comment = CaseComment.objects.create(case=case, author=request.user, text=serializer.validated_data["text"])
            CaseEvent.objects.create(case=case, type=CaseEvent.COMMENT_ADDED, actor=request.user, detail=comment.text)
            Case.objects.filter(pk=case.pk).update(updated_at=timezone.now())
            AuditLog.record(AuditLog.CASE_COMMENT_ADDED, actor=request.user, target=case, request=request, details={"reference": case.reference})
        return Response(CaseCommentSerializer(comment).data, status=status.HTTP_201_CREATED)

    @extend_schema(
        tags=["Cases"],
        summary="Escalate a case",
        description=(
            "**Escalate Case screen** (`/investigations/escalate`). Moves the case to `escalated` and records why "
            "and to whom.\n\n"
            "**Fields:**\n"
            "- `reason` (required): `financial_threshold` (exceeds financial approval limit), `legal_risk` "
            "(potential legal or regulatory violation), `complex_fraud` (requires specialized fraud analysis), "
            "`conflict_of_interest` (identified conflict of interest)\n"
            "- `escalate_to_id` (required): an active member of your organisation (super admin or team member)\n"
            "- `priority` (required): `critical`, `high`, `medium`, `low`; becomes the case priority and moves the "
            "SLA due date\n"
            "- `notes` (optional, up to 2000)\n\n"
            "Supporting documents are attached through evidence uploads, added by a later feature.\n\n"
            "A case can be escalated again (for example to someone else); the latest escalation is shown in "
            "`escalation`. A closed case is 400: reopen it first. 200 returns the updated case; show \"Case "
            "successfully escalated\". Recorded in the history (`escalated`) and the audit log.\n\n" + AUTH_NOTE
        ),
        parameters=[REFERENCE_PARAM],
        request=CaseEscalateSerializer,
        examples=[
            OpenApiExample(
                "Escalate",
                value={"reason": "financial_threshold", "escalate_to_id": "9a8b7c6d-5e4f-4a3b-9c2d-1e0f9a8b7c6d", "priority": "critical", "notes": "Above the 1,000,000 NGN approval limit."},
                request_only=True,
            ),
        ],
        responses={
            200: OpenApiResponse(response=CaseDetailSerializer, examples=[OpenApiExample("Escalated", value=ESCALATED_EXAMPLE)]),
            400: OpenApiResponse(
                response=OpenApiTypes.OBJECT,
                examples=[
                    OpenApiExample("Closed case", value={"status": [CLOSED_CASE_MESSAGE]}),
                    OpenApiExample("Unknown reason", value={"reason": ['"budget" is not a valid choice.']}),
                    OpenApiExample("Not a member", value={"escalate_to_id": [REVIEWER_INVALID_MESSAGE]}),
                    OpenApiExample("Missing fields", value={"reason": ["This field is required."], "escalate_to_id": ["This field is required."], "priority": ["This field is required."]}),
                ],
            ),
            401: UNAUTHORIZED, 403: FORBIDDEN, 404: NOT_FOUND,
        },
    )
    @action(detail=True, methods=["post"])
    def escalate(self, request, reference=None):
        case = self.get_object()
        serializer = CaseEscalateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        if case.status == "closed":
            raise serializers.ValidationError({"status": [CLOSED_CASE_MESSAGE]})
        person = _member(request.user.organisation, data["escalate_to_id"])
        if person is None:
            raise serializers.ValidationError({"escalate_to_id": [REVIEWER_INVALID_MESSAGE]})
        old_status = case.status
        with transaction.atomic():
            case.status = "escalated"
            case.priority = data["priority"]
            case.escalation_reason = data["reason"]
            case.escalated_to = person
            case.escalation_notes = data.get("notes", "").strip()
            case.escalated_at = timezone.now()
            case.save(update_fields=["status", "priority", "escalation_reason", "escalated_to", "escalation_notes", "escalated_at", "updated_at"])
            CaseEvent.objects.create(
                case=case, type=CaseEvent.ESCALATED, actor=request.user, from_status=old_status, to_status="escalated",
                detail=f"Escalated to {person.full_name}: {case.get_escalation_reason_display()}",
            )
            AuditLog.record(
                AuditLog.CASE_ESCALATED, actor=request.user, target=case, request=request,
                details={"reference": case.reference, "reason": case.escalation_reason, "escalated_to": str(person.pk), "priority": case.priority},
            )
        return self._detail(case)
