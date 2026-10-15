import uuid

from django.conf import settings
from django.db import models

from accounts.models import Organisation


class Alert(models.Model):
    """One fraud finding for an organisation, as the Alerts screens show it."""

    DUPLICATE_BANK_ACCOUNT = "duplicate_bank_account"
    DUPLICATE_PAYMENT = "duplicate_payment"
    RULE_CHOICES = [
        (DUPLICATE_BANK_ACCOUNT, "Duplicate Bank Account"),
        (DUPLICATE_PAYMENT, "Duplicate Payment"),
    ]
    SEVERITY_CHOICES = [("critical", "Critical"), ("high", "High"), ("medium", "Medium"), ("low", "Low")]
    STATUS_CHOICES = [
        ("new", "New"),
        ("investigating", "Investigating"),
        ("escalated", "Escalated"),
        ("false_positive", "False Positive"),
    ]
    CONFIDENCE_SOURCE_CHOICES = [("derived", "Derived from the risk score"), ("ml", "Machine learning")]
    ENTITY_TYPE_CHOICES = [("employee", "Employee"), ("vendor", "Vendor")]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organisation = models.ForeignKey(Organisation, on_delete=models.PROTECT, related_name="alerts")
    number = models.PositiveIntegerField(help_text="Per-organisation sequence behind the ALT- reference.")
    rule = models.CharField(max_length=50, choices=RULE_CHOICES)
    risk_type = models.CharField(max_length=100)
    title = models.CharField(max_length=255)
    explanation = models.TextField()
    severity = models.CharField(max_length=20, choices=SEVERITY_CHOICES)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="new")
    risk_score = models.PositiveSmallIntegerField()
    confidence = models.PositiveSmallIntegerField()
    confidence_source = models.CharField(max_length=20, choices=CONFIDENCE_SOURCE_CHOICES, default="derived")
    entity_type = models.CharField(max_length=20, choices=ENTITY_TYPE_CHOICES)
    entity_name = models.CharField(max_length=255)
    entity_ref = models.CharField(max_length=100)
    department = models.CharField(max_length=255, blank=True, default="")
    assigned_reviewer = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="assigned_alerts"
    )
    upload = models.ForeignKey(
        "ingestion.DataUpload", on_delete=models.PROTECT, null=True, blank=True, related_name="alerts"
    )
    flagged_records = models.JSONField(default=list, blank=True)
    timeline = models.JSONField(default=list, blank=True)
    fingerprint = models.CharField(max_length=255, help_text="rule:key; stops the same finding opening twice.")
    currency = models.CharField(max_length=3, default="NGN")
    # Reserved for ML (decision 8); empty until it fills them.
    ai_summary = models.TextField(blank=True, default="")
    ai_recommendations = models.JSONField(default=list, blank=True)
    pattern_detections = models.JSONField(default=list, blank=True)
    ai_confidence = models.PositiveSmallIntegerField(null=True, blank=True)
    detected_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "alerts"
        ordering = ["-detected_at", "-number"]
        constraints = [
            models.UniqueConstraint(fields=["organisation", "number"], name="unique_alert_number_per_organisation"),
            models.UniqueConstraint(fields=["organisation", "fingerprint"], name="unique_alert_fingerprint_per_organisation"),
        ]
        indexes = [
            models.Index(fields=["organisation", "status"]),
            models.Index(fields=["organisation", "severity"]),
        ]

    @property
    def reference(self) -> str:
        return f"ALT-{self.number:04d}"

    def __str__(self):
        return f"{self.reference} {self.risk_type} ({self.severity})"


class AlertEvent(models.Model):
    """One entry in an alert's history: created, status changed, reviewer changed, note added."""

    CREATED = "created"
    STATUS_CHANGED = "status_changed"
    REVIEWER_ASSIGNED = "reviewer_assigned"
    REVIEWER_REMOVED = "reviewer_removed"
    NOTE_ADDED = "note_added"
    TYPE_CHOICES = [
        (CREATED, "Alert created"),
        (STATUS_CHANGED, "Status changed"),
        (REVIEWER_ASSIGNED, "Reviewer assigned"),
        (REVIEWER_REMOVED, "Reviewer removed"),
        (NOTE_ADDED, "Note added"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    alert = models.ForeignKey(Alert, on_delete=models.CASCADE, related_name="events")
    type = models.CharField(max_length=30, choices=TYPE_CHOICES)
    # Null actor means the system (detection).
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    from_status = models.CharField(max_length=20, blank=True, default="")
    to_status = models.CharField(max_length=20, blank=True, default="")
    detail = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "alert_events"
        ordering = ["created_at"]


class AlertNote(models.Model):
    """A note on an alert, such as the "Instruction to Reviewer". Notes are never edited."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    alert = models.ForeignKey(Alert, on_delete=models.CASCADE, related_name="notes")
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+")
    text = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "alert_notes"
        ordering = ["-created_at"]

