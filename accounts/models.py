import uuid
from django.contrib.auth.models import AbstractUser
from django.db import models
from django.db.models.functions import Lower


class Organisation(models.Model):
    """A customer Cooperative. Names are unique, ignoring letter case."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)

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