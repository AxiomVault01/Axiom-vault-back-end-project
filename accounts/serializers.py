from django.core.validators import RegexValidator
from rest_framework import serializers
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password

User = get_user_model()

class SendOTPSerializer(serializers.Serializer):
    email = serializers.EmailField(required=True)

class VerifyOTPSerializer(serializers.Serializer):
    email = serializers.EmailField(required=True)
    code = serializers.CharField(
        required=True,
        validators=[RegexValidator(r"^[0-9]{6}$", message="Enter the 6-digit code.")],
    )

class ResendOTPSerializer(serializers.Serializer):
    email = serializers.EmailField(required=True)

DEPARTMENT_CHOICES = [
    ("internal_audit", "Internal Audit"),
    ("finance", "Finance"),
    ("compliance", "Compliance"),
    ("risk_management", "Risk Management"),
    ("human_resources", "Human Resources"),
    ("others", "Others"),
]


class SignupSerializer(serializers.Serializer):
    verification_token = serializers.CharField(required=True)
    full_name = serializers.CharField(max_length=255, required=True)
    organization = serializers.CharField(max_length=255, required=True)
    department = serializers.ChoiceField(choices=DEPARTMENT_CHOICES, required=True)

    # Strength is checked in AuthService.signup, where the email and name are known.
    password = serializers.CharField(write_only=True, required=True)
    re_enter_password = serializers.CharField(write_only=True, required=True)

    def validate(self, data):
        if data['password'] != data['re_enter_password']:
            raise serializers.ValidationError({"re_enter_password": "Passwords do not match."})
        return data

class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField(
        required=True, help_text="The account's email address. Letter case does not matter."
    )
    password = serializers.CharField(
        write_only=True, required=True, help_text="The account's password."
    )

class ResetPasswordSerializer(serializers.Serializer):
    email = serializers.EmailField(required=True)
    otp_code = serializers.CharField(max_length=6, required=True)
    new_password = serializers.CharField(write_only=True, required=True, validators=[validate_password])
