from django.contrib import admin
from .models import Payroll

@admin.register(Payroll)
class PayrollAdmin(admin.ModelAdmin):
    list_display = (
        "employee_name",
        "account_number",   # ✅ correct
        "net_salary",       # ✅ correct
        "payment_date",
    )