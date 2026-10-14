from django.contrib import admin

from .models import Payroll, PayrollUpload


@admin.register(PayrollUpload)
class PayrollUploadAdmin(admin.ModelAdmin):
    list_display = ("file_name", "organisation", "row_count", "uploaded_by", "created_at")
    list_select_related = ("organisation", "uploaded_by")
    search_fields = ("file_name", "organisation__name")
    ordering = ("-created_at",)


@admin.register(Payroll)
class PayrollAdmin(admin.ModelAdmin):
    list_display = ("employee_name", "employee_id", "account_number", "net_salary", "pay_period", "organisation")
    list_select_related = ("organisation",)
    search_fields = ("employee_name", "employee_id", "account_number")
    list_filter = ("pay_period",)
