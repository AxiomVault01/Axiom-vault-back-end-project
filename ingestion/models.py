import uuid

from django.conf import settings
from django.db import models

from accounts.models import Organisation


class DataUpload(models.Model):
    """One accepted file (payroll, vendors, or payments): who sent it, for which organisation, how many rows."""

    PAYROLL = "payroll"
    VENDORS = "vendors"
    PAYMENTS = "payments"
    KIND_CHOICES = [(PAYROLL, "Payroll"), (VENDORS, "Vendors"), (PAYMENTS, "Payments")]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    kind = models.CharField(max_length=20, choices=KIND_CHOICES, default=PAYROLL)
    organisation = models.ForeignKey(Organisation, on_delete=models.PROTECT, related_name="data_uploads")
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    file_name = models.CharField(max_length=255)
    row_count = models.PositiveIntegerField()
    # The original file, kept private on Cloudinary as evidence. Empty for uploads made before 7d.
    source_public_id = models.CharField(max_length=255, blank=True, default="")
    file_size = models.PositiveIntegerField(default=0, help_text="Size of the original file in bytes.")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "data_uploads"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.file_name} ({self.kind}, {self.row_count} rows)"


class Payroll(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # Every row belongs to one organisation and the upload it came from.
    organisation = models.ForeignKey(Organisation, on_delete=models.PROTECT, related_name="payroll_rows")
    upload = models.ForeignKey(DataUpload, on_delete=models.PROTECT, related_name="rows")

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


class Vendor(models.Model):
    """One row of a vendors file. Each upload is a snapshot of the organisation's vendor list."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organisation = models.ForeignKey(Organisation, on_delete=models.PROTECT, related_name="vendor_rows")
    upload = models.ForeignKey(DataUpload, on_delete=models.PROTECT, related_name="vendors")
    vendor_id = models.CharField(max_length=100)
    vendor_name = models.CharField(max_length=255)
    account_number = models.CharField(max_length=20)
    bank_name = models.CharField(max_length=100, null=True, blank=True)
    registration_number = models.CharField(max_length=100, null=True, blank=True)
    address = models.CharField(max_length=255, null=True, blank=True)
    phone = models.CharField(max_length=20, blank=True, default="")
    email = models.CharField(max_length=254, blank=True, default="")
    date_registered = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "vendors"
        indexes = [
            models.Index(fields=["organisation", "vendor_id"]),
            models.Index(fields=["account_number"]),
        ]

    def __str__(self):
        return f"{self.vendor_name} ({self.vendor_id})"


class Payment(models.Model):
    """One row of a payments file: a payment to a vendor, linked to it by `vendor_id`."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organisation = models.ForeignKey(Organisation, on_delete=models.PROTECT, related_name="payment_rows")
    upload = models.ForeignKey(DataUpload, on_delete=models.PROTECT, related_name="payments")
    transaction_id = models.CharField(max_length=100)
    vendor_id = models.CharField(max_length=100)
    invoice_number = models.CharField(max_length=100)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    payment_date = models.DateField()
    approver = models.CharField(max_length=255, null=True, blank=True)
    cost_centre = models.CharField(max_length=100, null=True, blank=True)
    department = models.CharField(max_length=255, null=True, blank=True)
    description = models.CharField(max_length=500, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "payments"
        indexes = [
            models.Index(fields=["organisation", "vendor_id"]),
            models.Index(fields=["invoice_number"]),
            models.Index(fields=["payment_date"]),
        ]

    def __str__(self):
        return f"{self.transaction_id}: {self.amount} to {self.vendor_id}"



class StoredFile(models.Model):
    """Any file a member uploaded (case evidence and other attachments), kept privately on Cloudinary."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organisation = models.ForeignKey(Organisation, on_delete=models.PROTECT, related_name="stored_files")
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    file_name = models.CharField(max_length=255)
    content_type = models.CharField(max_length=100)
    size = models.PositiveIntegerField(help_text="Size in bytes.")
    # Private Cloudinary id; never returned by the API.
    public_id = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "stored_files"
        ordering = ["-created_at"]

    def __str__(self):
        return self.file_name
