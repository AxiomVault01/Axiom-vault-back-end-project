import math

from django.utils import timezone
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from config.file_storage import StorageUnavailable
from ingestion.file_viewsets import StoredFileSerializer, describe

from .models import Case, CaseComment, CaseEvent
from .serializers import NOTE_MAX_LENGTH, ActorSerializer, AlertListSerializer, PersonSerializer, actor, person

CASE_STATUS_VALUES = [value for value, _ in Case.STATUS_CHOICES]
PRIORITY_VALUES = [value for value, _ in Case.PRIORITY_CHOICES]
REASON_VALUES = [value for value, _ in Case.ESCALATION_REASON_CHOICES]
MAX_ALERTS_PER_CASE = 20
MAX_ESCALATION_DOCUMENTS = 10


def evidence_item(evidence):
    """One row of the Evidence box. The link is `null` if storage can't be reached, so the page still loads."""
    try:
        file = describe(evidence.file)
    except StorageUnavailable:
        file = describe(evidence.file, link=False)
    return {
        "id": str(evidence.id),
        "source": evidence.source,
        "file": file,
        "added_by": actor(evidence.added_by),
        "added_at": evidence.created_at,
    }


class CaseEvidenceSerializer(serializers.Serializer):
    id = serializers.UUIDField(help_text="The evidence entry's ID; use it to remove the evidence.")
    source = serializers.CharField(help_text="`evidence` (Evidence box) or `escalation` (an escalation supporting document).")
    file = StoredFileSerializer(help_text="The attached file, with a download link valid for 1 hour.")
    added_by = ActorSerializer(allow_null=True, help_text="`{id, full_name}` of who attached it, or `null` if removed.")
    added_at = serializers.DateTimeField(help_text="When it was attached (ISO 8601).")


class EvidenceCreateSerializer(serializers.Serializer):
    file_id = serializers.UUIDField(help_text="ID returned by `POST /api/v1/files/`. Must be a file of your organisation.")


class EmployeeSerializer(serializers.Serializer):
    id = serializers.CharField(help_text="Employee ID from the payroll file.")
    name = serializers.CharField(help_text="Employee name.")
    department = serializers.CharField(help_text="Department, or empty.")


class VendorSerializer(serializers.Serializer):
    id = serializers.CharField(help_text="Vendor ID from the payments file.")
    name = serializers.CharField(help_text="Vendor name.")


class RelatedSerializer(serializers.Serializer):
    employees = EmployeeSerializer(many=True, help_text="Employees named in the linked alerts.")
    vendors = VendorSerializer(many=True, help_text="Vendors named in the linked alerts.")
    approvers = serializers.ListField(child=serializers.CharField(), help_text="Approver names on the flagged payments.")


class EscalationSerializer(serializers.Serializer):
    reason = serializers.CharField(help_text="`financial_threshold`, `legal_risk`, `complex_fraud`, or `conflict_of_interest`.")
    reason_label = serializers.CharField(help_text="Text for the reason, e.g. `Exceeds financial approval limit`.")
    escalated_to = PersonSerializer(allow_null=True, help_text="Who the case was escalated to, or `null` if removed.")
    notes = serializers.CharField(help_text="Escalation notes, or empty.")
    escalated_at = serializers.DateTimeField(help_text="When it was escalated.")


class CaseListSerializer(serializers.ModelSerializer):
    """One row of the Investigations table."""

    reference = serializers.CharField(read_only=True, help_text="Readable ID, e.g. `CASE-0001`. Use it in every case URL.")
    investigator = serializers.SerializerMethodField(help_text="`{id, full_name, email}` of the investigator, or `null`.")
    alerts_count = serializers.SerializerMethodField(help_text="How many alerts the case links.")
    evidence_count = serializers.SerializerMethodField(help_text="How many evidence files are attached (Evidence column).")
    sla_due_at = serializers.DateTimeField(read_only=True, help_text="When the SLA runs out: created + 2 (critical), 5 (high), 7 (medium), or 14 (low) days.")
    sla_days_remaining = serializers.SerializerMethodField(
        help_text="Whole days left, rounded up (\"2 days remaining\"); 0 or negative when overdue; `null` once closed."
    )
    days_open = serializers.SerializerMethodField(help_text="Whole days since it was opened (until closed, if closed).")

    class Meta:
        model = Case
        fields = [
            "reference", "title", "status", "priority", "investigator", "alerts_count", "evidence_count", "sla_due_at",
            "sla_days_remaining", "days_open", "created_at", "updated_at",
        ]
        read_only_fields = fields
        extra_kwargs = {
            "title": {"help_text": "Case title."},
            "status": {"help_text": "`new`, `investigating`, `under_review`, `escalated`, or `closed`."},
            "priority": {"help_text": "`critical`, `high`, `medium`, or `low`."},
            "created_at": {"help_text": "When the case was opened (ISO 8601)."},
            "updated_at": {"help_text": "Last change (ISO 8601)."},
        }

    @extend_schema_field(PersonSerializer(allow_null=True))
    def get_investigator(self, case):
        return person(case.investigator)

    @extend_schema_field(serializers.IntegerField())
    def get_alerts_count(self, case):
        count = getattr(case, "alerts_count", None)
        return case.alerts.count() if count is None else count

    @extend_schema_field(serializers.IntegerField())
    def get_evidence_count(self, case):
        count = getattr(case, "evidence_count", None)
        return case.evidence.count() if count is None else count

    @extend_schema_field(serializers.IntegerField(allow_null=True))
    def get_sla_days_remaining(self, case):
        if case.status == "closed":
            return None
        return math.ceil((case.sla_due_at - timezone.now()).total_seconds() / 86400)

    @extend_schema_field(serializers.IntegerField())
    def get_days_open(self, case):
        return ((case.closed_at or timezone.now()) - case.created_at).days


class CaseDetailSerializer(CaseListSerializer):
    """Everything the Case detail screen shows."""

    reviewer = serializers.SerializerMethodField(help_text="`{id, full_name, email}` of the reviewer, or `null`.")
    created_by = serializers.SerializerMethodField(help_text="`{id, full_name}` of who opened it, or `null`.")
    escalation = serializers.SerializerMethodField(help_text="Escalation details, or `null` if never escalated.")
    alerts = serializers.SerializerMethodField(help_text="The linked alerts, in the Alerts-table shape.")
    related = serializers.SerializerMethodField(help_text="Employees, vendors, and approvers from the linked alerts.")
    transactions = serializers.SerializerMethodField(
        help_text="Flagged payments of the linked alerts (connected transactions), each with its `alert` reference. "
        "Amounts are text with 2 decimals in `currency`."
    )
    comments_count = serializers.SerializerMethodField(help_text="How many comments the case has.")

    class Meta(CaseListSerializer.Meta):
        fields = CaseListSerializer.Meta.fields + [
            "description", "reviewer", "created_by", "escalation", "alerts", "related", "transactions",
            "comments_count", "closed_at", "currency", "ai_summary", "ai_recommendations",
        ]
        read_only_fields = fields
        extra_kwargs = {
            **CaseListSerializer.Meta.extra_kwargs,
            "description": {"help_text": "Longer description, or empty."},
            "closed_at": {"help_text": "When it was closed, or `null` while open."},
            "currency": {"help_text": "Currency of every amount, `NGN`."},
            "ai_summary": {"help_text": "Reserved for ML. Empty for now."},
            "ai_recommendations": {"help_text": "Reserved for ML. `[]` for now."},
        }

    def _alerts(self, case):
        if not hasattr(self, "_linked"):
            self._linked = list(case.alerts.select_related("assigned_reviewer").order_by("number"))
        return self._linked

    @extend_schema_field(PersonSerializer(allow_null=True))
    def get_reviewer(self, case):
        return person(case.reviewer)

    @extend_schema_field(ActorSerializer(allow_null=True))
    def get_created_by(self, case):
        return actor(case.created_by)

    @extend_schema_field(EscalationSerializer(allow_null=True))
    def get_escalation(self, case):
        if not case.escalated_at:
            return None
        return {
            "reason": case.escalation_reason,
            "reason_label": case.get_escalation_reason_display(),
            "escalated_to": person(case.escalated_to),
            "notes": case.escalation_notes,
            "escalated_at": case.escalated_at,
        }

    @extend_schema_field(AlertListSerializer(many=True))
    def get_alerts(self, case):
        return AlertListSerializer(self._alerts(case), many=True).data

    @extend_schema_field(RelatedSerializer)
    def get_related(self, case):
        employees, vendors, approvers = {}, {}, []
        for alert in self._alerts(case):
            if alert.entity_type == "vendor":
                vendors.setdefault(alert.entity_ref, {"id": alert.entity_ref, "name": alert.entity_name})
            else:
                employees.setdefault(alert.entity_ref, {"id": alert.entity_ref, "name": alert.entity_name, "department": alert.department})
            for record in alert.flagged_records:
                if record.get("employee_id"):
                    employees.setdefault(record["employee_id"], {
                        "id": record["employee_id"], "name": record.get("employee_name", ""), "department": record.get("department", ""),
                    })
                if record.get("approver") and record["approver"] not in approvers:
                    approvers.append(record["approver"])
        return {"employees": list(employees.values()), "vendors": list(vendors.values()), "approvers": approvers}

    @extend_schema_field(serializers.ListField(child=serializers.DictField()))
    def get_transactions(self, case):
        return [
            {**record, "alert": alert.reference}
            for alert in self._alerts(case)
            for record in alert.flagged_records
            if record.get("transaction_id")
        ]

    @extend_schema_field(serializers.IntegerField())
    def get_comments_count(self, case):
        return case.comments.count()


class CaseEventSerializer(serializers.ModelSerializer):
    actor = serializers.SerializerMethodField(help_text="`{id, full_name}` of who did it, or `null` if removed.")

    class Meta:
        model = CaseEvent
        fields = ["id", "type", "actor", "from_status", "to_status", "detail", "created_at"]
        read_only_fields = fields
        extra_kwargs = {
            "type": {
                "help_text": "`created`, `status_changed`, `investigator_assigned`, `investigator_removed`, "
                "`reviewer_assigned`, `reviewer_removed`, `comment_added`, or `escalated`."
            },
            "from_status": {"help_text": "Status before a status change or escalation, else empty."},
            "to_status": {"help_text": "Status after it (or at creation), else empty."},
            "detail": {"help_text": "Extra text: the note, the person's name, the comment, or the escalation reason."},
        }

    @extend_schema_field(ActorSerializer(allow_null=True))
    def get_actor(self, event):
        return actor(event.actor)


class CaseCommentSerializer(serializers.ModelSerializer):
    author = serializers.SerializerMethodField(help_text="`{id, full_name}` of who wrote it, or `null` if removed.")

    class Meta:
        model = CaseComment
        fields = ["id", "text", "author", "created_at"]
        read_only_fields = ["id", "author", "created_at"]
        extra_kwargs = {"text": {"help_text": f"The comment, 1 to {NOTE_MAX_LENGTH} characters.", "max_length": NOTE_MAX_LENGTH}}

    @extend_schema_field(ActorSerializer(allow_null=True))
    def get_author(self, comment):
        return actor(comment.author)


class CaseCreateSerializer(serializers.Serializer):
    alerts = serializers.ListField(
        child=serializers.CharField(max_length=20), min_length=1, max_length=MAX_ALERTS_PER_CASE,
        help_text=f"References of the alerts to investigate together, 1 to {MAX_ALERTS_PER_CASE}, e.g. `[\"ALT-0002\"]`.",
    )
    title = serializers.CharField(
        required=False, allow_blank=True, max_length=255,
        help_text="Case title. Defaults to the first alert's title (plus `(+N more alerts)`).",
    )
    description = serializers.CharField(required=False, allow_blank=True, max_length=NOTE_MAX_LENGTH, help_text="Optional description.")
    investigator_id = serializers.UUIDField(required=False, allow_null=True, help_text="Optional: an active member of your organisation.")


class CaseStatusSerializer(serializers.Serializer):
    status = serializers.ChoiceField(
        choices=CASE_STATUS_VALUES,
        help_text="`new`, `investigating`, `under_review`, or `closed`. `escalated` is set by the escalate endpoint.",
    )
    note = serializers.CharField(required=False, allow_blank=True, max_length=NOTE_MAX_LENGTH, help_text="Optional reason, saved in the history.")


class CaseAssignmentSerializer(serializers.Serializer):
    investigator_id = serializers.UUIDField(required=False, allow_null=True, help_text="Investigator: an active member's ID, or `null` to remove. Leave out to keep.")
    reviewer_id = serializers.UUIDField(required=False, allow_null=True, help_text="Reviewer: an active member's ID, or `null` to remove. Leave out to keep.")

    def validate(self, data):
        if not data:
            raise serializers.ValidationError("Send `investigator_id`, `reviewer_id`, or both.")
        return data


class CaseEscalateSerializer(serializers.Serializer):
    reason = serializers.ChoiceField(
        choices=REASON_VALUES,
        help_text="`financial_threshold`, `legal_risk`, `complex_fraud`, or `conflict_of_interest`.",
    )
    escalate_to_id = serializers.UUIDField(help_text="ID of an active member of your organisation (super admin or team member).")
    priority = serializers.ChoiceField(choices=PRIORITY_VALUES, help_text="Priority level: `critical`, `high`, `medium`, or `low`.")
    notes = serializers.CharField(required=False, allow_blank=True, max_length=NOTE_MAX_LENGTH, help_text="Escalation notes, up to 2000 characters.")
    document_ids = serializers.ListField(
        child=serializers.UUIDField(), required=False, max_length=MAX_ESCALATION_DOCUMENTS,
        help_text=f"Optional supporting documents: up to {MAX_ESCALATION_DOCUMENTS} IDs from `POST /api/v1/files/`, each a "
        "CSV, XLS, or XLSX file (at most 10 MB). They are attached to the case as evidence with `source: \"escalation\"`.",
    )


class CaseListFilterSerializer(serializers.Serializer):
    """Checks the list's query parameters."""

    status = serializers.ChoiceField(choices=CASE_STATUS_VALUES, required=False)
    priority = serializers.ChoiceField(choices=PRIORITY_VALUES, required=False)
    investigator = serializers.CharField(required=False)
    search = serializers.CharField(required=False)

    def validate_investigator(self, value):
        if value == "none":
            return value
        try:
            return str(serializers.UUIDField().to_internal_value(value))
        except serializers.ValidationError:
            raise serializers.ValidationError("Use a user ID or `none`.")
