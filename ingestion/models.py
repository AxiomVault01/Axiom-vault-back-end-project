import uuid

from django.conf import settings
from django.db import models

from accounts.models import Organisation


class PayrollUpload(models.Model):
    """One accepted payroll file: who sent it, for which organisation, and how many rows it held."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organisation = models.ForeignKey(Organisation, on_delete=models.PROTECT, related_name="payroll_uploads")
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    file_name = models.CharField(max_length=255)
    row_count = models.PositiveIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "payroll_uploads"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.file_name} ({self.row_count} rows)"


class Payroll(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # Every row belongs to one organisation and the upload it came from.
    organisation = models.ForeignKey(Organisation, on_delete=models.PROTECT, related_name="payroll_rows")
    upload = models.ForeignKey(PayrollUpload, on_delete=models.PROTECT, related_name="rows")

    # ✅ CORE
    employee_id = models.CharField(max_length=100)
    employee_name = models.CharField(max_length=255)
    department = models.CharField(max_length=255)

    salary_basic = models.DecimalField(max_digits=12, decimal_places=2)
    net_salary = models.DecimalField(max_digits=12, decimal_places=2)

    account_number = models.CharField(max_length=20)
    pay_period = models.CharField(max_length=20)
    payment_date = models.DateField()

    # ⚙️ EXTENDED
    role = models.CharField(max_length=100, null=True, blank=True)
    allowances = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    deductions = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    bank_name = models.CharField(max_length=100, null=True, blank=True)

    # 🚀 ADVANCED
    attendance_days = models.IntegerField(null=True, blank=True)
    leave_days = models.IntegerField(null=True, blank=True)
    location = models.CharField(max_length=100, null=True, blank=True)
    supervisor_id = models.CharField(max_length=100, null=True, blank=True)
    employment_type = models.CharField(max_length=50, null=True, blank=True)
    grade_level = models.CharField(max_length=50, null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "payroll"
        indexes = [
            models.Index(fields=["employee_id"]),
            models.Index(fields=["account_number"]),
            models.Index(fields=["pay_period"]),
        ]

    def __str__(self):
        return f"{self.employee_name} - {self.pay_period}"
