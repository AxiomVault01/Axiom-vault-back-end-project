import math
import secrets
import string
from django.db import transaction
from django.utils import timezone
from datetime import timedelta
from django.contrib.auth import get_user_model, authenticate, login as django_login, logout as django_logout
from django.core.signing import BadSignature, TimestampSigner
from kombu.exceptions import OperationalError
from rest_framework import status
from rest_framework.exceptions import APIException, ValidationError, AuthenticationFailed
from .models import OTP
from .tasks import send_otp_email_task


User = get_user_model()

VERIFICATION_CODE_COOLDOWN_SECONDS = 60
ACCOUNT_EXISTS_MESSAGE = "An account with this email already exists. Please sign in."
CODE_TOO_SOON_MESSAGE = "Please wait before requesting a new code."
DELIVERY_FAILED_MESSAGE = "We could not send the verification code. Please try again shortly."

MAX_CODE_ATTEMPTS = 3
SIGNUP_TOKEN_MAX_AGE_SECONDS = 30 * 60
SIGNUP_TOKEN_SALT = "accounts.signup-verification"
NO_ACTIVE_CODE_MESSAGE = "No active code for this email. Please request a new code."
CODE_EXPIRED_MESSAGE = "This code has expired. Please request a new code."
INCORRECT_CODE_MESSAGE = "Incorrect code."
TOO_MANY_ATTEMPTS_MESSAGE = "Too many incorrect attempts. Please request a new code."
TOKEN_INVALID_MESSAGE = "Your email verification has expired. Please verify your email again."


class SignupVerificationToken:
    """Signed proof that an email passed code verification; readable for 30 minutes."""

    @staticmethod
    def issue(email: str) -> str:
        return TimestampSigner(salt=SIGNUP_TOKEN_SALT).sign(email)

    @staticmethod
    def read(token: str) -> str:
        try:
            return TimestampSigner(salt=SIGNUP_TOKEN_SALT).unsign(token, max_age=SIGNUP_TOKEN_MAX_AGE_SECONDS)
        except BadSignature:
            raise ValidationError({"error": TOKEN_INVALID_MESSAGE})


class CodeRequestTooSoon(APIException):
    status_code = status.HTTP_429_TOO_MANY_REQUESTS

    def __init__(self, retry_after: int):
        super().__init__()
        self.detail = {"error": CODE_TOO_SOON_MESSAGE, "retry_after": retry_after}


class CodeDeliveryUnavailable(APIException):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    def __init__(self):
        super().__init__()
        self.detail = {"error": DELIVERY_FAILED_MESSAGE}


class CodeVerificationFailed(APIException):
    """400 that keeps numbers (attempts_remaining) as numbers, unlike ValidationError."""

    status_code = status.HTTP_400_BAD_REQUEST

    def __init__(self, detail: dict):
        super().__init__()
        self.detail = detail


class OTPService:
    """Manages generation, expiration, and validation of 6-digit OTP codes."""

    @staticmethod
    def send_verification_code(email: str):
        """Sends a sign-up code to an email that has no account yet, at most once per cooldown."""
        if User.objects.filter(email__iexact=email).exists():
            raise ValidationError({"error": ACCOUNT_EXISTS_MESSAGE})

        latest = OTP.objects.filter(email__iexact=email, purpose="verification").order_by("-created_at").first()
        if latest:
            elapsed = (timezone.now() - latest.created_at).total_seconds()
            if elapsed < VERIFICATION_CODE_COOLDOWN_SECONDS:
                raise CodeRequestTooSoon(retry_after=math.ceil(VERIFICATION_CODE_COOLDOWN_SECONDS - elapsed))

        try:
            # Queueing the email inside the transaction means a broker failure also
            # undoes the new code and the invalidation of older ones.
            with transaction.atomic():
                OTPService.generate(email, purpose="verification")
        except OperationalError:
            raise CodeDeliveryUnavailable()

    @staticmethod
    def verify_signup_code(email: str, code: str) -> str:
        """Checks a sign-up code and returns a verification token; wrong guesses are counted."""
        error = None
        with transaction.atomic():
            otp = (
                OTP.objects.select_for_update()
                .filter(email__iexact=email, purpose="verification", is_used=False)
                .order_by("-created_at")
                .first()
            )
            if otp is None:
                error = {"error": NO_ACTIVE_CODE_MESSAGE}
            elif otp.is_expired:
                otp.is_used = True
                otp.save(update_fields=["is_used"])
                error = {"error": CODE_EXPIRED_MESSAGE}
            elif not secrets.compare_digest(otp.code, code):
                otp.failed_attempts += 1
                attempts_remaining = MAX_CODE_ATTEMPTS - otp.failed_attempts
                if attempts_remaining <= 0:
                    otp.is_used = True
                    error = {"error": TOO_MANY_ATTEMPTS_MESSAGE}
                else:
                    error = {"error": INCORRECT_CODE_MESSAGE, "attempts_remaining": attempts_remaining}
                otp.save(update_fields=["failed_attempts", "is_used"])
            else:
                otp.is_used = True
                otp.save(update_fields=["is_used"])

        # Raised after the block commits, so counted attempts and cancelled codes stay saved.
        if error:
            raise CodeVerificationFailed(error)
        return SignupVerificationToken.issue(otp.email)

    @staticmethod
    def generate(email: str, purpose="verification"):
        OTP.objects.filter(
            email__iexact=email,
            purpose=purpose,
            is_used=False
        ).update(is_used=True)

        code = "".join(secrets.choice(string.digits) for _ in range(6))

        OTP.objects.create(
            email=email,
            code=code,
            purpose=purpose,
            expires_at=timezone.now() + timedelta(minutes=2)
        )

        send_otp_email_task.delay(
            email,
            code
        )

        return code    
    
    @staticmethod
    def verify(email: str, code: str, purpose: str = "verification") -> tuple[bool, str | None]:
        otp_record = OTP.objects.filter(email=email, code=code, purpose=purpose).first()

        if not otp_record:
            return False, "Invalid validation code provided."
        if otp_record.is_used:
            return False, "This token has already been consumed."
        if otp_record.is_expired:
            return False, "Verification token code has expired."

        otp_record.is_used = True
        otp_record.save()
        return True, None


class AuthService:
    """Manages user accounts, creation, login sessions, and password recovery."""

    @staticmethod
    def signup(validated_data: dict) -> User:
        """Handles the complete user onboarding and initial verification trigger."""
        email = validated_data["email"]
        base_username = email.split('@')[0]

        user = User.objects.create_user(
            username=base_username,
            email=email,
            password=validated_data["password"],
            full_name=validated_data["full_name"],
            organization=validated_data["organization"],
            department=validated_data["department"],
            role=validated_data["role"],
            is_verified=False
        )
        
        # Fire off verification token
        OTPService.generate(user.email, purpose="verification")
        return user

    @staticmethod
    def login(request, email: str, password: str) -> User:
        """Validates credentials and binds a session to the incoming request."""
        user = authenticate(request, username=email, password=password)
        
        if not user:
            raise AuthenticationFailed("Invalid authentication credentials.")
            
        django_login(request, user)
        return user

    @staticmethod
    def forgot_password(email: str) -> bool:
        """Triggers a password recovery event if the target account exists."""
        if User.objects.filter(email=email).exists():
            OTPService.generate(email, purpose="password_reset")
            return True
        return False

    @staticmethod
    def reset_password(email: str, code: str, new_password: str):
        """Verifies the reset code and updates account credentials safely."""
        valid, error = OTPService.verify(email, code, purpose="password_reset")
        if not valid:
            raise ValidationError({"otp_code": error})

        try:
            user = User.objects.get(email=email)
            user.set_password(new_password)
            user.save()
        except User.DoesNotExist:
            raise ValidationError({"email": "Target account context missing."})

    @staticmethod
    def logout(request):
        """Safely terminates an active authenticated session."""
        if not request.user.is_authenticated:
            raise ValidationError({"detail": "No active authentication active."})
        django_logout(request)
