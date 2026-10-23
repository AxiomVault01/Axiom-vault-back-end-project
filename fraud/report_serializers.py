from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from .case_serializers import CaseCommentSerializer, CaseDetailSerializer, CaseEventSerializer
from .investigation_serializers import AnalysisSerializer, InvestigationRelatedSerializer
from .models import Alert, CaseEvidence
from .serializers import ActorSerializer, actor


class ReportAlertSerializer(serializers.ModelSerializer):
    """An alert as the report shows it: the alert-table fields plus why it was flagged."""

    reference = serializers.CharField(read_only=True, help_text="Readable ID, e.g. `ALT-0002`.")

    class Meta:
        model = Alert
        fields = [
            "reference", "title", "risk_type", "rule", "severity", "status", "risk_score", "confidence", "entity_type",
            "entity_name", "entity_ref", "department", "explanation", "flagged_records", "detected_at",
        ]
        read_only_fields = fields
        extra_kwargs = {
            "title": {"help_text": "Plain-language headline."},
            "risk_type": {"help_text": "Risk type label, e.g. `Duplicate Payment`."},
            "rule": {"help_text": "Rule code: `duplicate_bank_account`, `duplicate_payment`, or `payment_outside_pay_period`."},
            "severity": {"help_text": "`critical`, `high`, `medium`, or `low`."},
            "status": {"help_text": "`new`, `investigating`, `escalated`, or `false_positive`."},
            "risk_score": {"help_text": "0-100."},
            "confidence": {"help_text": "0-100."},
            "entity_type": {"help_text": "`employee` or `vendor`."},
            "entity_name": {"help_text": "Employee or vendor name."},
            "entity_ref": {"help_text": "Their employee or vendor ID."},
            "department": {"help_text": "Department, or empty."},
            "explanation": {"help_text": "Why it was flagged."},
            "flagged_records": {"help_text": "The records that triggered it, as on the alert detail; amounts are text with 2 decimals."},
            "detected_at": {"help_text": "When detection opened it (ISO 8601)."},
        }


class ReportEvidenceSerializer(serializers.ModelSerializer):
    """An evidence file as listed in the report: no download link, because the report outlives the link."""

    file_name = serializers.CharField(source="file.file_name", help_text="Name of the file.")
    content_type = serializers.CharField(source="file.content_type", help_text="File type, e.g. `application/pdf`.")
    size = serializers.IntegerField(source="file.size", help_text="Size in bytes.")
    added_by = serializers.SerializerMethodField(help_text="`{id, full_name, profile_picture}` of who attached it, or `null`.")
    added_at = serializers.DateTimeField(source="created_at", help_text="When it was attached (ISO 8601).")

    class Meta:
        model = CaseEvidence
        fields = ["file_name", "content_type", "size", "source", "added_by", "added_at"]
        read_only_fields = fields
        extra_kwargs = {"source": {"help_text": "`evidence` (Evidence box) or `escalation` (escalation supporting document)."}}

    @extend_schema_field(ActorSerializer(allow_null=True))
    def get_added_by(self, evidence):
        return actor(evidence.added_by)


class ReportSerializer(serializers.Serializer):
    """The evidence summary of one case; the PDF and Excel exports hold exactly this."""

    case = CaseDetailSerializer(help_text="The case, as `GET /api/v1/fraud/cases/{reference}/` returns it.")
    alerts = ReportAlertSerializer(many=True, help_text="The case's alerts, oldest reference first, with explanations and flagged records.")
    analysis = AnalysisSerializer(help_text="Transaction analysis, as the investigation endpoint returns it.")
    related = InvestigationRelatedSerializer(help_text="Related employees, vendors, and approvers.")
    evidence = ReportEvidenceSerializer(many=True, help_text="Attached files, oldest first.")
    comments = CaseCommentSerializer(many=True, help_text="Every comment, oldest first.")
    history = CaseEventSerializer(many=True, help_text="The full case history, oldest first.")
    generated_at = serializers.DateTimeField(help_text="When this report was produced (ISO 8601).")
    generated_by = serializers.SerializerMethodField(help_text="`{id, full_name, profile_picture}` of who produced it.")

    @extend_schema_field(ActorSerializer)
    def get_generated_by(self, data):
        return actor(data["generated_by"])
