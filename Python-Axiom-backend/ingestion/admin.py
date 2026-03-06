from django.contrib import admin
from .models import PayrollRecord


@admin.register(PayrollRecord)
class PayrollAdmin(admin.ModelAdmin):
    list_display = (
        "employee_id",
        "bank_account_id",
        "amount_paid",
        "pay_period",
        "created_at",
    )
    search_fields = ("employee_id", "bank_account_id")
