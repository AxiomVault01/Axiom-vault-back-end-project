from rest_framework import serializers
from .models import Payroll


class PayrollSerializer(serializers.ModelSerializer):

    class Meta:
        model = Payroll
        fields = "__all__"
        read_only_fields = ["id", "created_at"]

class PayrollUploadSerializer(serializers.Serializer):
    file = serializers.FileField(
        help_text="The payroll file: .csv or .xlsx, at most 10 MB.",
        label="Payroll file"
    )
