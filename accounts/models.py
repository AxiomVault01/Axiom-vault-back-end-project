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


# ✅ ADD THIS
class OTP(models.Model):

    TYPE_CHOICES = [
        ("signup", "Signup"),
        ("reset", "Password Reset"),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE)

    code = models.CharField(max_length=4)
    type = models.CharField(max_length=20, choices=TYPE_CHOICES)

    is_used = models.BooleanField(default=False)
    attempt_count = models.IntegerField(default=0)

    expires_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.user.email} - {self.code}"