from rest_framework import viewsets, status
from rest_framework.response import Response

from .models import PayrollRecord, BankAccount
from .serializers import PayrollSerializer, BankAccountSerializer
from .services.ingestion_service import PayrollService
from .services.validation_service import BankAccountService


class PayrollViewSet(viewsets.ModelViewSet):

    queryset = PayrollRecord.objects.all()
    serializer_class = PayrollSerializer

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        PayrollService.ingest(serializer.validated_data)

        return Response(
            {"message": "Payroll ingested successfully"},
            status=status.HTTP_201_CREATED,
        )
    
class BankAccountViewSet(viewsets.ModelViewSet):

    queryset = BankAccount.objects.all()
    serializer_class = BankAccountSerializer

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        BankAccountService.ingest(serializer.validated_data)

        return Response(
            {"message": "Bank account ingested successfully"},
            status=status.HTTP_201_CREATED
        )