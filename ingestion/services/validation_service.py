from ingestion.models import BankAccount


class BankAccountService:

    @staticmethod
    def ingest(data):
        return BankAccount.objects.create(**data)