from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response

from fraud.models import AlertAuditLog, Alert
from .serializers import (
    FraudResultSerializer,
    AlertSerializer,
    AlertAuditSerializer
)

from .services.fraud_query_service import FraudQueryService
from .services.alert_service import AlertService


class FraudResultViewSet(viewsets.ReadOnlyModelViewSet):

    serializer_class = FraudResultSerializer

    def get_queryset(self):
        return FraudQueryService.get_all()

    @action(detail=False, methods=["get"])
    def flagged(self, request):
        results = FraudQueryService.get_flagged()
        serializer = self.get_serializer(results, many=True)
        return Response(serializer.data)


class AlertViewSet(viewsets.ModelViewSet):

    queryset = Alert.objects.all()
    serializer_class = AlertSerializer

    @action(detail=True, methods=["patch"])
    def change_status(self, request, pk=None):

        alert = self.get_object()
        new_status = request.data.get("status")

        AlertService.change_status(
            alert=alert,
            new_status=new_status,
            user=str(request.user)
        )

        return Response({"message": "Status updated successfully"})

    @action(detail=True, methods=["get"])
    def audit_logs(self, request, pk=None):

        alert = self.get_object()
        logs = alert.audit_logs.all()

        serializer = AlertAuditSerializer(logs, many=True)
        return Response(serializer.data)