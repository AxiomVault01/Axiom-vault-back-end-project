import uuid
from django.contrib.auth.models import AbstractUser
from django.db import models
from django.core.exceptions import PermissionDenied
from django.db.models.functions import Lower

from .device import UNKNOWN_IP, client_ip


class Organisation(models.Model):
    """A customer Cooperative. Names are unique, ignoring letter case."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    # Seeded demo data for the frontend; `manage.py clear_demo` removes it before launch.
    is_demo = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    # Suspension by AxiomVault staff (feature 20): its users can't log in or use the API.
    ACTIVE = "active"
    SUSPENDED = "suspended"
    STATUS_CHOICES = [(ACTIVE, "Active"), (SUSPENDED, "Suspended")]
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=ACTIVE)
    suspended_at = models.DateTimeField(null=True, blank=True)
    suspension_reason = models.TextField(blank=True, default="")

    @property
    def is_suspended(self) -> bool:
        return self.status == self.SUSPENDED

    class Meta:
        constraints = [
            models.UniqueConstraint(Lower("name"), name="unique_organisation_name_ci"),
        ]

    def __str__(self):
        return self.name


class User(AbstractUser):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    full_name = models.CharField(max_length=255)
    # Empty only for Django staff/superusers, who belong to no Cooperative.
    organisation = models.ForeignKey(
        Organisation, on_delete=models.PROTECT, null=True, blank=True, related_name="users"
    )
    department = models.CharField(max_length=255)

    email = models.EmailField(unique=True)
    is_verified = models.BooleanField(default=False)

    # Free text the super admin writes (a job title). A label only; account_type decides access.
    role = models.CharField(max_length=100, blank=True, default="")
    phone_number = models.CharField(max_length=20, blank=True, default="")
    # Set for a team member's temporary password; cleared by change or reset password.
    must_change_password = models.BooleanField(default=False)
    # Public Cloudinary image under an unguessable id; empty when the user has none (feature 32).
    profile_picture_url = models.URLField(max_length=500, blank=True, default="")
    profile_picture_public_id = models.CharField(max_length=255, blank=True, default="")

    SUPER_ADMIN = "super_admin"
    TEAM_MEMBER = "team_member"
    ACCOUNT_TYPE_CHOICES = [
        (SUPER_ADMIN, "Super Admin"),
        (TEAM_MEMBER, "Team Member"),
    ]
    # Empty only for Django staff/superusers, who belong to no Cooperative.
    account_type = models.CharField(max_length=20, choices=ACCOUNT_TYPE_CHOICES, blank=True, default="")

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["username"]

    class Meta(AbstractUser.Meta):
        constraints = [
            models.UniqueConstraint(
                fields=["organisation"],
                condition=models.Q(account_type="super_admin"),
                name="one_super_admin_per_organisation",
            ),
        ]

    def __str__(self):
        return self.email


# ✅ OTP MODEL
from django.db import models
from django.utils import timezone
from datetime import timedelta
import uuid


import uuid
from datetime import timedelta

from django.db import models
from django.utils import timezone


class OTP(models.Model):

    PURPOSE_CHOICES = (
        ("verification", "Email Verification"),
        ("login", "Login"),
        ("password_reset", "Password Reset"),
    )

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )

    email = models.EmailField(
        db_index=True
    )

    code = models.CharField(
        max_length=6,
        db_index=True
    )

    purpose = models.CharField(
        max_length=50,
        choices=PURPOSE_CHOICES,
        default="verification",
    )

    is_used = models.BooleanField(default=False)

    failed_attempts = models.PositiveSmallIntegerField(default=0)

    expires_at = models.DateTimeField()

    created_at = models.DateTimeField(
        auto_now_add=True
    )

    class Meta:
        db_table = "otp_codes"
        ordering = ["-created_at"]

    def save(self, *args, **kwargs):
        if not self.expires_at:
            self.expires_at = timezone.now() + timedelta(minutes=5)

        super().save(*args, **kwargs)

    @property
    def is_expired(self):
        return timezone.now() > self.expires_at

    @property
    def is_valid(self):
        return (
            not self.is_used
            and not self.is_expired
        )

class AuditLogQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise PermissionDenied("Audit log rows cannot be changed.")

    def delete(self):
        raise PermissionDenied("Audit log rows cannot be deleted.")


class AuditLog(models.Model):
    """One append-only row per important action: who, in which organisation, what, to what, when."""

    SIGNUP = "signup"
    LOGIN = "login"
    LOGOUT = "logout"
    PASSWORD_CHANGED = "password_changed"
    PASSWORD_RESET = "password_reset"
    TEAM_MEMBER_ADDED = "team_member_added"
    TEAM_MEMBER_UPDATED = "team_member_updated"
    TEAM_MEMBER_DEACTIVATED = "team_member_deactivated"
    TEAM_MEMBER_REACTIVATED = "team_member_reactivated"
    PAYROLL_UPLOADED = "payroll_uploaded"
    VENDORS_UPLOADED = "vendors_uploaded"
    PAYMENTS_UPLOADED = "payments_uploaded"
    FILE_UPLOADED = "file_uploaded"
    ORGANISATION_CREATED = "organisation_created"
    ORGANISATION_UPDATED = "organisation_updated"
    ORGANISATION_SUSPENDED = "organisation_suspended"
    ORGANISATION_REACTIVATED = "organisation_reactivated"
    PROFILE_UPDATED = "profile_updated"
    SETTINGS_UPDATED = "settings_updated"
    PROFILE_PICTURE_UPDATED = "profile_picture_updated"
    PROFILE_PICTURE_REMOVED = "profile_picture_removed"
    ALERT_CREATED = "alert_created"
    ALERT_STATUS_CHANGED = "alert_status_changed"
    ALERT_REVIEWER_CHANGED = "alert_reviewer_changed"
    ALERT_NOTE_ADDED = "alert_note_added"
    CASE_CREATED = "case_created"
    CASE_STATUS_CHANGED = "case_status_changed"
    CASE_ASSIGNED = "case_assigned"
    CASE_COMMENT_ADDED = "case_comment_added"
    CASE_ESCALATED = "case_escalated"
    CASE_EVIDENCE_ADDED = "case_evidence_added"
    CASE_EVIDENCE_REMOVED = "case_evidence_removed"
    ACTION_CHOICES = [
        (SIGNUP, "Signed up"),
        (LOGIN, "Logged in"),
        (LOGOUT, "Logged out"),
        (PASSWORD_CHANGED, "Changed password"),
        (PASSWORD_RESET, "Reset password"),
        (TEAM_MEMBER_ADDED, "Added a team member"),
        (TEAM_MEMBER_UPDATED, "Edited a team member"),
        (TEAM_MEMBER_DEACTIVATED, "Deactivated a team member"),
        (TEAM_MEMBER_REACTIVATED, "Reactivated a team member"),
        (PAYROLL_UPLOADED, "Uploaded a payroll file"),
        (VENDORS_UPLOADED, "Uploaded a vendors file"),
        (PAYMENTS_UPLOADED, "Uploaded a payments file"),
        (FILE_UPLOADED, "Uploaded a file"),
        (ORGANISATION_CREATED, "Created an organisation"),
        (ORGANISATION_UPDATED, "Edited an organisation"),
        (ORGANISATION_SUSPENDED, "Suspended an organisation"),
        (ORGANISATION_REACTIVATED, "Reactivated an organisation"),
        (PROFILE_UPDATED, "Edited their profile"),
        (SETTINGS_UPDATED, "Changed their settings"),
        (PROFILE_PICTURE_UPDATED, "Changed their profile picture"),
        (PROFILE_PICTURE_REMOVED, "Removed their profile picture"),
        (ALERT_CREATED, "Detection opened an alert"),
        (ALERT_STATUS_CHANGED, "Changed an alert's status"),
        (ALERT_REVIEWER_CHANGED, "Changed an alert's reviewer"),
        (ALERT_NOTE_ADDED, "Added a note to an alert"),
        (CASE_CREATED, "Opened a case"),
        (CASE_STATUS_CHANGED, "Changed a case's status"),
        (CASE_ASSIGNED, "Changed a case's investigator or reviewer"),
        (CASE_COMMENT_ADDED, "Commented on a case"),
        (CASE_ESCALATED, "Escalated a case"),
        (CASE_EVIDENCE_ADDED, "Added evidence to a case"),
        (CASE_EVIDENCE_REMOVED, "Removed evidence from a case"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    action = models.CharField(max_length=50, choices=ACTION_CHOICES)
    # The copies keep a row meaningful after the user or organisation is deleted.
    actor = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    actor_email = models.CharField(max_length=254, blank=True, default="")
    organisation = models.ForeignKey(
        Organisation, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    organisation_name = models.CharField(max_length=255, blank=True, default="")
    target_type = models.CharField(max_length=50, blank=True, default="")
    target_id = models.CharField(max_length=64, blank=True, default="")
    details = models.JSONField(default=dict, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)

    objects = AuditLogQuerySet.as_manager()

    class Meta:
        db_table = "audit_logs"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["organisation", "created_at"]),
            models.Index(fields=["action"]),
        ]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise PermissionDenied("Audit log rows cannot be changed.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionDenied("Audit log rows cannot be deleted.")

    @classmethod
    def record(cls, action, actor=None, target=None, details=None, request=None, organisation=None):
        """Writes one row. The organisation is the actor's unless given (system actions have no actor).

        Never put passwords or tokens in details.
        """
        organisation = organisation or getattr(actor, "organisation", None)
        ip = client_ip(request.META) if request is not None else None
        return cls.objects.create(
            action=action,
            actor=actor,
            actor_email=getattr(actor, "email", "") or "",
            organisation=organisation,
            organisation_name=organisation.name if organisation else "",
            target_type=target._meta.model_name if target is not None else "",
            target_id=str(target.pk) if target is not None else "",
            details=details or {},
            ip_address=None if ip in (None, UNKNOWN_IP) else ip,
        )


class UserSettings(models.Model):
    """A user's Settings screen preferences (feature 18). Created with these defaults on first read.

    The notification switches are stored for a later notification feature; nothing sends notifications yet.
    """

    LANGUAGE_CHOICES = [("en-US", "English (US)"), ("en-GB", "English (UK)"), ("es", "Spanish")]
    DATE_FORMAT_CHOICES = [("DD/MM/YYYY", "DD/MM/YYYY"), ("MM/DD/YYYY", "MM/DD/YYYY"), ("YYYY-MM-DD", "YYYY-MM-DD")]

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="settings")
    critical_risk_alerts = models.BooleanField(default=True)
    high_risk_alerts = models.BooleanField(default=True)
    daily_summary_reports = models.BooleanField(default=False)
    system_updates = models.BooleanField(default=True)
    language = models.CharField(max_length=10, choices=LANGUAGE_CHOICES, default="en-US")
    # An IANA time zone name, checked against the system's time zone database.
    time_zone = models.CharField(max_length=64, default="Africa/Lagos")
    date_format = models.CharField(max_length=10, choices=DATE_FORMAT_CHOICES, default="DD/MM/YYYY")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "user_settings"

    def __str__(self):
        return f"Settings of {self.user}"
