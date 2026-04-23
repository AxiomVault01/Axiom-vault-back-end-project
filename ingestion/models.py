import uuid
from django.db import models


class Payroll(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

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