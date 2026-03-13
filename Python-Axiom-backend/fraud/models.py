import uuid
from django.db import models


# -----------------------------
# Fraud Detection Results
# -----------------------------
class FraudResult(models.Model):

    STATUS_CHOICES = (
        ("CLEAR", "Clear"),
        ("FLAGGED", "Flagged"),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    payroll_id = models.UUIDField()
    risk_score = models.FloatField()

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES
    )

    analyzed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "fraud_results"
        ordering = ["-analyzed_at"]

    def __str__(self):
        return f"{self.payroll_id} - {self.status}"


# -----------------------------
# Fraud Alerts
# -----------------------------
class Alert(models.Model):

    STATUS_CHOICES = (
        ("OPEN", "Open"),
        ("IN_REVIEW", "In Review"),
        ("CLOSED", "Closed"),
    )

    SEVERITY_CHOICES = (
        ("LOW", "Low"),
        ("MEDIUM", "Medium"),
        ("HIGH", "High"),
        ("CRITICAL", "Critical"),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    payroll_id = models.UUIDField()

    risk_score = models.FloatField()

    severity = models.CharField(
        max_length=20,
        choices=SEVERITY_CHOICES
    )

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="OPEN"
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "alerts"
        ordering = ["-created_at"]

    def __str__(self):
        return f"Alert {self.id} - {self.status}"


# -----------------------------
# Alert Audit Logs
# -----------------------------
class AlertAuditLog(models.Model):

    ACTION_CHOICES = (
        ("CREATED", "Created"),
        ("STATUS_CHANGED", "Status Changed"),
        ("COMMENT_ADDED", "Comment Added"),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    alert = models.ForeignKey(
        Alert,
        on_delete=models.CASCADE,
        related_name="audit_logs"
    )

    action = models.CharField(
        max_length=30,
        choices=ACTION_CHOICES
    )

    previous_status = models.CharField(
        max_length=20,
        null=True,
        blank=True
    )

    new_status = models.CharField(
        max_length=20,
        null=True,
        blank=True
    )

    performed_by = models.CharField(max_length=100)

    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "alert_audit_logs"
        ordering = ["-timestamp"]

    def __str__(self):
        return f"{self.action} on {self.alert.id}"