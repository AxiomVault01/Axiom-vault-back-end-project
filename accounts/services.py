import random
import string
from django.utils import timezone
from datetime import timedelta
from django.contrib.auth import get_user_model, authenticate, login as django_login, logout as django_logout
from rest_framework.exceptions import ValidationError, AuthenticationFailed
from .models import OTP
from .tasks import send_otp_email_task


User = get_user_model()

class OTPService:
    """Manages generation, expiration, and validation of 6-digit OTP codes."""
    

    @staticmethod
    def generate(email: str, purpose="verification"):

        print(f"GENERATING OTP FOR -> {email}")

        OTP.objects.filter(
            email=email,
            purpose=purpose,
            is_used=False
        ).update(is_used=True)

        code = "".join(random.choices(string.digits, k=6))

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
