import uuid
from django.db import models


class PayrollRecord(models.Model):

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    employee_id = models.CharField(max_length=100)

    bank_account = models.ForeignKey(
        "BankAccount",
        on_delete=models.CASCADE,
        related_name="payrolls"
    )

    amount_paid = models.DecimalField(max_digits=12, decimal_places=2)
    pay_period = models.CharField(max_length=50)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "payroll_records"
        ordering = ["-created_at"]        
        indexes = [
            models.Index(fields=["employee_id"]),
            models.Index(fields=["pay_period"]),
        ]

class BankAccount(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    account_number = models.CharField(max_length=20)
    bank_name = models.CharField(max_length=100)
    account_name = models.CharField(max_length=150)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "bank_accounts"

    def __str__(self):
        return f"{self.account_name} ({self.account_number})"