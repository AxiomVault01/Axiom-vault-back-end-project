from fraud.models import FraudResult


class FraudQueryService:

    @staticmethod
    def get_all():
        return FraudResult.objects.all()

    @staticmethod
    def get_flagged():
        return FraudResult.objects.filter(status="FLAGGED")