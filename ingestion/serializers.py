from rest_framework import serializers
from .models import Payroll


class PayrollSerializer(serializers.ModelSerializer):

    class Meta:
        model = Payroll
        fields = "__all__"
        read_only_fields = ["id", "created_at"]

class FileUploadSerializer(serializers.Serializer):
    file = serializers.FileField(
        help_text="The file: .csv or .xlsx, at most 10 MB.",
        label="File"
    )


PERIOD_ORDER_MESSAGE = "The pay period must end on or after its start date."
DATE_INPUTS = ["%Y-%m-%d", "%d/%m/%Y"]


class PayrollUploadSerializer(FileUploadSerializer):
    """The payroll upload form: the file plus the pay period the uploader sets."""

    pay_period_start = serializers.DateField(input_formats=DATE_INPUTS, help_text="First day of the pay period, e.g. `2026-10-02` or `02/10/2026`.")
    pay_period_end = serializers.DateField(input_formats=DATE_INPUTS, help_text="Last day of the pay period (same day or later), e.g. `2026-10-07`.")

    def validate(self, data):
        if data["pay_period_end"] < data["pay_period_start"]:
            raise serializers.ValidationError({"pay_period_end": [PERIOD_ORDER_MESSAGE]})
        return data
