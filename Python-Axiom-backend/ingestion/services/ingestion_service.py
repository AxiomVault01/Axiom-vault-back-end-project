from ingestion.models import PayrollRecord
from fraud.services.fraud_analysis_service import FraudAnalysisService


class PayrollService:

    @staticmethod
    def ingest(data):
        payroll = PayrollRecord.objects.create(**data)

        # Trigger analysis
        FraudAnalysisService.analyze_payroll(payroll)

        return payroll
