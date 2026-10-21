from django.contrib.auth import get_user_model
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from .models import Alert, AlertEvent, AlertNote

User = get_user_model()
STATUS_VALUES = [value for value, _ in Alert.STATUS_CHOICES]
NOTE_MAX_LENGTH = 2000


def person(user):
    """A user as alert and case screens show them: `{id, full_name, email, profile_picture}`, or None."""
    if user is None:
        return None
    return {"id": str(user.id), "full_name": user.full_name, "email": user.email, "profile_picture": user.profile_picture_url or None}


def actor(user):
    """Who did something (history, notes, comments): `{id, full_name, profile_picture}`, or None."""
    if user is None:
        return None
    return {"id": str(user.id), "full_name": user.full_name, "profile_picture": user.profile_picture_url or None}


class PersonSerializer(serializers.Serializer):
    id = serializers.UUIDField(help_text="The user's ID.")
    full_name = serializers.CharField(help_text="Name shown in the app.")
    email = serializers.EmailField(help_text="The user's login email.")
    profile_picture = serializers.URLField(allow_null=True, help_text="Public URL of their profile picture, or `null` (show initials).")


class ActorSerializer(serializers.Serializer):
    id = serializers.UUIDField(help_text="The user's ID.")
    full_name = serializers.CharField(help_text="Name shown in the app.")
    profile_picture = serializers.URLField(allow_null=True, help_text="Public URL of their profile picture, or `null` (show initials).")


class UploadSummarySerializer(serializers.Serializer):
    id = serializers.UUIDField(help_text="The upload's ID.")
    kind = serializers.CharField(help_text="`payroll`, `vendors`, or `payments`.")
    file_name = serializers.CharField(help_text="Name of the uploaded file.")
    created_at = serializers.DateTimeField(help_text="When it was uploaded.")


class AlertListSerializer(serializers.ModelSerializer):
    """One row of the Alerts table."""

    reference = serializers.CharField(read_only=True, help_text="Readable ID, e.g. `ALT-0001`. Use it in every alert URL.")
    assigned_reviewer = serializers.SerializerMethodField(help_text="Who reviews it, or `null`.")

    class Meta:
        model = Alert
        fields = [
            "reference", "risk_type", "rule", "title", "entity_type", "entity_name", "entity_ref", "department",
            "severity", "status", "risk_score", "confidence", "assigned_reviewer", "detected_at",
        ]
        read_only_fields = fields
        extra_kwargs = {
            "risk_type": {"help_text": "Label for the Risk Type column, e.g. `Duplicate Payment`."},
            "rule": {"help_text": "Stable rule code: `duplicate_bank_account` or `duplicate_payment`."},
            "title": {"help_text": "Plain-language headline for the alert."},
            "entity_type": {"help_text": "`employee` or `vendor`: what the Entity column refers to."},
            "entity_name": {"help_text": "Employee or vendor name for the Entity column."},
            "entity_ref": {"help_text": "Their employee or vendor ID."},
            "department": {"help_text": "Department, or an empty string when the data had none."},
            "severity": {"help_text": "`critical`, `high`, `medium`, or `low`."},
            "status": {"help_text": "`new`, `investigating`, `escalated`, or `false_positive`."},
            "risk_score": {"help_text": "0-100."},
            "confidence": {"help_text": "0-100, shown as a percentage."},
            "detected_at": {"help_text": "When detection opened the alert (ISO 8601)."},
        }

    @extend_schema_field(PersonSerializer(allow_null=True))
    def get_assigned_reviewer(self, alert):
        return person(alert.assigned_reviewer)


class AlertDetailSerializer(AlertListSerializer):
    """Everything the Alert detail screen shows."""

    upload = serializers.SerializerMethodField(help_text="The upload that triggered the alert, or `null`.")
    evidence = serializers.SerializerMethodField(help_text="Evidence files of the case this alert is in, newest first; `[]` when it is in no case.")
    notes_count = serializers.SerializerMethodField(help_text="How many notes the alert has.")
    case = serializers.SerializerMethodField(help_text="Reference of the case investigating it (`CASE-0001`), or `null`.")

    class Meta(AlertListSerializer.Meta):
        fields = AlertListSerializer.Meta.fields + [
            "explanation", "confidence_source", "flagged_records", "timeline", "evidence", "currency", "upload",
            "notes_count", "case", "ai_summary", "ai_recommendations", "pattern_detections", "ai_confidence", "updated_at",
        ]
        read_only_fields = fields
        extra_kwargs = {
            **AlertListSerializer.Meta.extra_kwargs,
            "explanation": {"help_text": "The \"Why This Was Flagged\" text."},
            "confidence_source": {"help_text": "`derived` (equals the risk score) or `ml` (from machine learning)."},
            "flagged_records": {"help_text": "Rows for the Flagged Records table. Money values are text with 2 decimals."},
            "timeline": {"help_text": "Event Timeline entries `{date, label, detail}`, oldest first."},
            "currency": {"help_text": "Currency of every amount, `NGN`."},
            "ai_summary": {"help_text": "Reserved for ML. Empty for now."},
            "ai_recommendations": {"help_text": "Reserved for ML. `[]` for now."},
            "pattern_detections": {"help_text": "Reserved for ML. `[]` for now."},
            "ai_confidence": {"help_text": "Reserved for ML. `null` for now."},
            "updated_at": {"help_text": "Last change (ISO 8601)."},
        }

    @extend_schema_field(UploadSummarySerializer(allow_null=True))
    def get_upload(self, alert):
        upload = alert.upload
        if upload is None:
            return None
        return {"id": str(upload.id), "kind": upload.kind, "file_name": upload.file_name, "created_at": upload.created_at}

    @extend_schema_field(serializers.ListField(child=serializers.DictField()))
    def get_evidence(self, alert):
        if not alert.case_id:
            return []
        from .case_serializers import evidence_item  # Imported here: case_serializers imports this module.

        return [evidence_item(e) for e in alert.case.evidence.select_related("file__uploaded_by", "added_by")]

    @extend_schema_field(serializers.IntegerField())
    def get_notes_count(self, alert):
        return alert.notes.count()

    @extend_schema_field(serializers.CharField(allow_null=True))
    def get_case(self, alert):
        return alert.case.reference if alert.case_id else None


class AlertEventSerializer(serializers.ModelSerializer):
    actor = serializers.SerializerMethodField(help_text="`{id, full_name}`, or `null` when the system did it.")

    class Meta:
        model = AlertEvent
        fields = ["id", "type", "actor", "from_status", "to_status", "detail", "created_at"]
        read_only_fields = fields
        extra_kwargs = {
            "type": {"help_text": "`created`, `status_changed`, `reviewer_assigned`, `reviewer_removed`, or `note_added`."},
            "from_status": {"help_text": "Status before a status change, else empty."},
            "to_status": {"help_text": "Status after a status change (or at creation), else empty."},
            "detail": {"help_text": "Extra text: the note given with a change, the reviewer's name, or the title."},
        }

    @extend_schema_field(ActorSerializer(allow_null=True))
    def get_actor(self, event):
        return actor(event.actor)


class AlertNoteSerializer(serializers.ModelSerializer):
    author = serializers.SerializerMethodField(help_text="`{id, full_name}` of who wrote it, or `null` if removed.")

    class Meta:
        model = AlertNote
        fields = ["id", "text", "author", "created_at"]
        read_only_fields = ["id", "author", "created_at"]
        extra_kwargs = {"text": {"help_text": f"The note, 1 to {NOTE_MAX_LENGTH} characters.", "max_length": NOTE_MAX_LENGTH}}

    @extend_schema_field(ActorSerializer(allow_null=True))
    def get_author(self, note):
        return actor(note.author)



class StatusChangeSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=STATUS_VALUES, help_text="New status: `new`, `investigating`, `escalated`, `false_positive`.")
    note = serializers.CharField(
        required=False, allow_blank=True, max_length=NOTE_MAX_LENGTH,
        help_text="Optional reason, saved in the history (e.g. why it is a false positive).",
    )


class ReviewerSerializer(serializers.Serializer):
    reviewer_id = serializers.UUIDField(allow_null=True, help_text="ID of an active member of your organisation, or `null` to remove.")


class AlertListFilterSerializer(serializers.Serializer):
    """Checks the list's query parameters."""

    status = serializers.ChoiceField(choices=STATUS_VALUES, required=False)
    severity = serializers.ChoiceField(choices=[v for v, _ in Alert.SEVERITY_CHOICES], required=False)
    rule = serializers.ChoiceField(choices=[v for v, _ in Alert.RULE_CHOICES], required=False)
    department = serializers.CharField(required=False)
    reviewer = serializers.CharField(required=False)
    search = serializers.CharField(required=False)

    def validate_reviewer(self, value):
        if value == "none":
            return value
        try:
            return str(serializers.UUIDField().to_internal_value(value))
        except serializers.ValidationError:
            raise serializers.ValidationError("Use a user ID or `none`.")
