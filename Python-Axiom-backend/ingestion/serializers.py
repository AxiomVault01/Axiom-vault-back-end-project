from rest_framework import serializers
from .models import BankAccount, PayrollRecord


class PayrollSerializer(serializers.ModelSerializer):

    class Meta:
        model = PayrollRecord
        fields = "__all__"
        read_only_fields = ["id", "created_at"]

class BankAccountSerializer(serializers.ModelSerializer):

    class Meta:
        model = BankAccount
        fields = "__all__"
        read_only_fields = ["id", "created_at"]
