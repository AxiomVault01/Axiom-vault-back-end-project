import uuid
from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    full_name = models.CharField(max_length=255)
    organization = models.CharField(max_length=255)
    department = models.CharField(max_length=255)

    email = models.EmailField(unique=True)
    is_verified = models.BooleanField(default=False)

    ROLE_CHOICES = [
        ("fraud_analyst", "Fraud Analyst"),
        ("compliance_officer", "Compliance Officer"),
        ("auditor", "External Auditor"),
        ("manager", "Customer Success Manager"),
    ]

    role = models.CharField(max_length=50, choices=ROLE_CHOICES)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["username"]

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

    expires_at = models.DateTimeField()

    created_at = models.DateTimeField(
        auto_now_add=True
    )

    class Meta:
        db_table = "otp_codes"
        ordering = ["-created_at"]

    def save(self, *args, **kwargs):
        if not self.expires_at:
            self.expires_at = timezone.now() + timedelta(minutes=2)

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