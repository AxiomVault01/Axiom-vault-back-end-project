from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from .serializers import AlertListSerializer


class SubjectSerializer(serializers.Serializer):
    type = serializers.CharField(help_text="`alert` or `case`: what was opened.")
    reference = serializers.CharField(help_text="Its reference, e.g. `ALT-0002` or `CASE-0001`.")


class RelatedEmployeeSerializer(serializers.Serializer):
    id = serializers.CharField(help_text="Employee ID from the payroll file.")
    name = serializers.CharField(help_text="Name from their latest payroll row (or the alert, if no row is stored).")
    role = serializers.CharField(help_text="Position from their latest payroll row, or empty.")
    department = serializers.CharField(help_text="Department, or empty.")


class RelatedVendorSerializer(serializers.Serializer):
    id = serializers.CharField(help_text="Vendor ID from the vendors and payments files.")
    name = serializers.CharField(help_text="Name from the latest vendors file (or the alert, if none is stored).")
    bank_name = serializers.CharField(help_text="Bank from the latest vendors file, or empty.")
    registration_number = serializers.CharField(help_text="Company registration number, or empty.")


class RelatedApproverSerializer(serializers.Serializer):
    id = serializers.CharField(help_text="Approver ID from the payroll file, or empty for a payment approver known only by name.")
    name = serializers.CharField(help_text="Approver name; empty when the approver ID matches no stored employee.")
    role = serializers.CharField(help_text="Their position from payroll, or empty.")
    department = serializers.CharField(help_text="Their department from payroll, or empty.")


class InvestigationRelatedSerializer(serializers.Serializer):
    employees = RelatedEmployeeSerializer(many=True, help_text="Employees the alerts are about or flag.")
    vendors = RelatedVendorSerializer(many=True, help_text="Vendors the alerts are about.")
    approvers = RelatedApproverSerializer(many=True, help_text="Who approved the flagged salaries and payments.")


class LinkedAlertSerializer(AlertListSerializer):
    case = serializers.SerializerMethodField(help_text="Reference of the case investigating it (`CASE-0001`), or `null`.")

    class Meta(AlertListSerializer.Meta):
        fields = AlertListSerializer.Meta.fields + ["case"]
        read_only_fields = fields

    @extend_schema_field(serializers.CharField(allow_null=True))
    def get_case(self, alert):
        return alert.case.reference if alert.case else None


class TransactionSerializer(serializers.Serializer):
    kind = serializers.CharField(help_text="`payment` (a vendor payment) or `salary` (a payroll row).")
    id = serializers.CharField(help_text="Transaction ID for a payment; employee ID for a salary.")
    description = serializers.CharField(help_text="Vendor and invoice, or `Salary: <name> (<pay period>)`.")
    amount = serializers.CharField(help_text="Amount as text with 2 decimals, e.g. `42,350.00`, in `analysis.currency`.")
    date = serializers.DateField(help_text="Payment date.")
    status = serializers.CharField(help_text="`flagged` when an alert on this page flags it, else `clear`.")
    alerts = serializers.ListField(child=serializers.CharField(), help_text="References of the alerts that flag it; `[]` when clear.")


class TransactionsSerializer(serializers.Serializer):
    count = serializers.IntegerField(help_text="How many connected transactions are stored in total.")
    results = TransactionSerializer(many=True, help_text="The newest 100, newest first.")


class AnalysisSerializer(serializers.Serializer):
    total_exposure = serializers.CharField(help_text="Sum of the flagged amounts, each transaction once.")
    duplicate_amount = serializers.CharField(
        help_text="Money paid in duplicate: for a duplicate payment the total minus the largest payment; for a "
        "shared bank account every salary after the first; 0 for other rules."
    )
    risk_score = serializers.IntegerField(help_text="Highest risk score of the alerts, 0-100.")
    transaction_count = serializers.IntegerField(help_text="How many distinct transactions the alerts flag.")
    currency = serializers.CharField(help_text="Currency of every amount, `NGN`.")
    source = serializers.CharField(help_text="`derived`: computed by the API until ML defines these numbers.")


class TimelineEventSerializer(serializers.Serializer):
    date = serializers.CharField(help_text="Date of the event (`YYYY-MM-DD`).")
    label = serializers.CharField(help_text="What happened, e.g. `Payment`, `Salary paid`, `Alert generated`.")
    detail = serializers.CharField(help_text="Sub-text, e.g. `TXN-90214: paid 42,350.00`.")
    alert = serializers.CharField(help_text="Reference of the alert the event belongs to.")


class InvestigationSerializer(serializers.Serializer):
    """Everything the investigation drill-down shows for an alert or a case."""

    subject = SubjectSerializer(help_text="What was opened.")
    alerts = AlertListSerializer(many=True, help_text="The alerts being investigated: the alert itself, or the case's alerts.")
    related = InvestigationRelatedSerializer(help_text="Related employees, vendors, and approvers.")
    linked_alerts = LinkedAlertSerializer(
        many=True, help_text="Other alerts about the same employees or vendors, newest first, at most 50."
    )
    transactions = TransactionsSerializer(help_text="Connected transactions: every stored payment and salary touching them.")
    analysis = AnalysisSerializer(help_text="Transaction analysis numbers.")
    pattern_detections = serializers.ListField(child=serializers.DictField(), help_text="Reserved for ML. `[]` for now.")
    timeline = TimelineEventSerializer(many=True, help_text="Transaction timeline from the alerts, oldest first.")
