from ingestion.models import PayrollRecord


class FraudAnalysisService:

    @staticmethod
    def analyze_payroll(payroll: PayrollRecord):
        """
        Temporary rule-based detection.
        ML model will replace this later.
        """

        risk_score = 0

        # Example rule
        if payroll.amount_paid > 500000:
            risk_score += 50

        if payroll.pay_period.endswith("12"):
            risk_score += 10

        return {
            "payroll_id": payroll.id,
            "risk_score": risk_score,
            "status": "FLAGGED" if risk_score > 40 else "CLEAR",
        }
