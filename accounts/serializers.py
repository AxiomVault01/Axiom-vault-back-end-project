import re
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
    organization = serializers.CharField(
        max_length=255,
        required=True,
        help_text="Name of the Cooperative this person represents, at most 255 characters. Signup "
        "creates a new organisation with this name. Names are unique, ignoring letter case and "
        "surrounding spaces, so a name that is already registered is refused.",
    )
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
    role = serializers.ChoiceField(
        choices=["super_admin", "team_member"],
        required=True,
        help_text="Which kind of account the user is signing in as, from the Sign In screen: "
        "`super_admin` (the person who registered the Cooperative) or `team_member` (added by "
        "the super admin). It must match the account, or login returns 403.",
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

class ChangePasswordSerializer(serializers.Serializer):
    current_password = serializers.CharField(
        write_only=True, required=True, help_text="The password the user logs in with today."
    )
    # Strength is checked in AuthService.change_password, where the user (email, name) is known.
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


PHONE_NUMBER_MESSAGE = "Enter a valid phone number: 7 to 15 digits, optionally starting with +."
EMAIL_TAKEN_MESSAGE = "An account with this email already exists."


def clean_phone_number(value: str) -> str:
    """Removes spaces and hyphens, then requires 7 to 15 digits with an optional leading +."""
    cleaned = value.replace(" ", "").replace("-", "")
    if not re.fullmatch(r"\+?[0-9]{7,15}", cleaned):
        raise serializers.ValidationError(PHONE_NUMBER_MESSAGE)
    return cleaned


class TeamMemberSerializer(serializers.ModelSerializer):
    """A team member as the super admin sees them."""

    profile_picture = serializers.SerializerMethodField(help_text="Public URL of the member's picture, or `null` (show initials).")

    class Meta:
        model = User
        fields = ["id", "full_name", "email", "phone_number", "role", "is_active", "must_change_password", "profile_picture", "date_joined"]
        read_only_fields = fields
        extra_kwargs = {
            "id": {"help_text": "The member's permanent ID; use it in the detail, edit, and (de)activate URLs."},
            "full_name": {"help_text": "Name shown in the app."},
            "email": {"help_text": "Login email, stored in lowercase."},
            "phone_number": {"help_text": "Digits with an optional leading +, spaces and hyphens removed."},
            "role": {"help_text": "Job title the super admin wrote, e.g. `Payroll Analyst`. A label only."},
            "is_active": {"help_text": "`false` after deactivation: the member cannot log in."},
            "must_change_password": {
                "help_text": "`true` until the member replaces the temporary password the super admin gave them."
            },
            "date_joined": {"help_text": "When the super admin added the member (ISO 8601)."},
        }

    def get_profile_picture(self, member) -> str | None:
        return member.profile_picture_url or None


class TeamMemberCreateSerializer(serializers.Serializer):
    full_name = serializers.CharField(max_length=255, help_text="Member's full name, at most 255 characters.")
    phone_number = serializers.CharField(
        max_length=30, help_text="7 to 15 digits, optionally starting with +; spaces and hyphens are removed."
    )
    role = serializers.CharField(
        max_length=100, help_text="Job title, free text up to 100 characters, e.g. `Payroll Analyst`."
    )
    email = serializers.EmailField(help_text="The member's login email. Must not belong to any other account.")
    password = serializers.CharField(
        write_only=True,
        help_text="Temporary password the super admin shares with the member. It must pass the normal "
        "password rules; the member has to replace it on first login.",
    )

    def validate_phone_number(self, value):
        return clean_phone_number(value)

    def validate_email(self, value):
        value = value.lower()
        if User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError(EMAIL_TAKEN_MESSAGE)
        return value


class TeamMemberUpdateSerializer(serializers.Serializer):
    """Every field is optional; only the fields sent are changed."""

    full_name = serializers.CharField(max_length=255, required=False, help_text="New full name.")
    phone_number = serializers.CharField(max_length=30, required=False, help_text="New phone number.")
    role = serializers.CharField(max_length=100, required=False, help_text="New job title.")
    email = serializers.EmailField(required=False, help_text="New login email; must not belong to another account.")

    def validate_phone_number(self, value):
        return clean_phone_number(value)

    def validate_email(self, value):
        value = value.lower()
        member = self.context["member"]
        if User.objects.filter(email__iexact=value).exclude(pk=member.pk).exists():
            raise serializers.ValidationError(EMAIL_TAKEN_MESSAGE)
        return value
