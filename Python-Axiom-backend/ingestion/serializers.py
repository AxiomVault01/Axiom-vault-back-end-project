from rest_framework import serializers
from .models import BankAccount, Payroll


class PayrollSerializer(serializers.ModelSerializer):

    class Meta:
        model = Payroll
        fields = "__all__"
        read_only_fields = ["id", "created_at"]

class BankAccountSerializer(serializers.ModelSerializer):

    class Meta:
        model = BankAccount
        fields = "__all__"
        read_only_fields = ["id", "created_at"]


class PayrollUploadSerializer(serializers.Serializer):
    file = serializers.FileField(
        help_text="CSV file for payroll data",
        label="Payroll CSV File"
    )
