from django.core.validators import RegexValidator
from rest_framework import serializers
from django.contrib.auth import get_user_model

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

class RefreshTokenSerializer(serializers.Serializer):
    refresh = serializers.CharField(
        required=True,
        help_text="The `refresh` token returned by login. Valid for 1 day after login.",
    )

class LogoutSerializer(serializers.Serializer):
    refresh = serializers.CharField(
        required=True,
        help_text="The `refresh` token returned by login. It is blacklisted and can never be used again.",
    )

class ForgotPasswordSerializer(serializers.Serializer):
    email = serializers.EmailField(
        required=True,
        help_text="The email of the account whose password was forgotten. Letter case does not matter.",
    )

class VerifyResetCodeSerializer(serializers.Serializer):
    email = serializers.EmailField(
        required=True, help_text="The same email that was sent to forgot-password."
    )
    code = serializers.CharField(
        required=True,
        validators=[RegexValidator(r"^[0-9]{6}$", message="Enter the 6-digit code.")],
        help_text="The 6-digit code from the password reset email.",
    )

class ResetPasswordSerializer(serializers.Serializer):
    reset_token = serializers.CharField(
        required=True, help_text="The `reset_token` returned by verify-reset-code. Works once, for 15 minutes."
    )
    # Strength is checked in AuthService.reset_password, where the user (email, name) is known.
    new_password = serializers.CharField(
        write_only=True,
        required=True,
        help_text="The new password. At least 8 characters, not too common, not only numbers, "
        "and not too similar to the email or name.",
    )
    re_enter_password = serializers.CharField(
        write_only=True, required=True, help_text="The new password again. Must match `new_password`."
    )

    def validate(self, data):
        if data["new_password"] != data["re_enter_password"]:
            raise serializers.ValidationError({"re_enter_password": "Passwords do not match."})
        return data
