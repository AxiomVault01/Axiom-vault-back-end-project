from rest_framework import serializers
from .models import FraudResult, Alert, AlertAuditLog


class FraudResultSerializer(serializers.ModelSerializer):
    class Meta:
        model = FraudResult
        fields = "__all__"


class AlertSerializer(serializers.ModelSerializer):
    class Meta:
        model = Alert
        fields = "__all__"


class AlertAuditSerializer(serializers.ModelSerializer):
    class Meta:
        model = AlertAuditLog
        fields = "__all__"