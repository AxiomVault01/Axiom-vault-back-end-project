from django.contrib import admin

from .models import DataUpload, Payment, Payroll, Vendor


@admin.register(DataUpload)
class DataUploadAdmin(admin.ModelAdmin):
    list_display = ("file_name", "kind", "organisation", "row_count", "uploaded_by", "created_at")
    list_filter = ("kind",)
    list_select_related = ("organisation", "uploaded_by")
    search_fields = ("file_name", "organisation__name")
    ordering = ("-created_at",)


@admin.register(Payroll)
class PayrollAdmin(admin.ModelAdmin):
    list_display = ("employee_name", "employee_id", "account_number", "net_salary", "pay_period", "organisation")
    list_select_related = ("organisation",)
    search_fields = ("employee_name", "employee_id", "account_number")
    list_filter = ("pay_period",)


@admin.register(Vendor)
class VendorAdmin(admin.ModelAdmin):
    list_display = ("vendor_name", "vendor_id", "account_number", "registration_number", "organisation")
    list_select_related = ("organisation",)
    search_fields = ("vendor_name", "vendor_id", "account_number")


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ("transaction_id", "vendor_id", "invoice_number", "amount", "payment_date", "organisation")
    list_select_related = ("organisation",)
    search_fields = ("transaction_id", "vendor_id", "invoice_number")
    list_filter = ("payment_date",)
