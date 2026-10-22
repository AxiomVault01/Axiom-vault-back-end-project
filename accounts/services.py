import logging
import math
import secrets
import string
import uuid
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone
from datetime import timedelta
from django.contrib.auth import authenticate
from django.contrib.auth.models import update_last_login
from django.contrib.auth.password_validation import validate_password
from django.contrib.auth.tokens import default_token_generator
from django.core.signing import BadSignature, TimestampSigner
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode
from kombu.exceptions import OperationalError
from rest_framework import status
from rest_framework.exceptions import APIException, ValidationError
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.settings import api_settings as jwt_settings
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken
from .models import OTP, Organisation, User
from .serializers import EMAIL_TAKEN_MESSAGE
from .tasks import send_otp_email_task, send_password_changed_email_task

logger = logging.getLogger(__name__)


VERIFICATION_CODE_COOLDOWN_SECONDS = 60
ACCOUNT_EXISTS_MESSAGE = "An account with this email already exists. Please sign in."
ORGANISATION_TAKEN_MESSAGE = "An organisation with this name already exists."
ORGANISATION_NAME_CONSTRAINT = "unique_organisation_name_ci"
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
SIGNUP_SUCCESS_MESSAGE = "Account created successfully. You can now sign in."
INVALID_CREDENTIALS_MESSAGE = "Invalid email or password."
EMAIL_NOT_VERIFIED_MESSAGE = "Your email is not verified. Please sign up again to verify your email."
WRONG_SIGN_IN_ROLE_MESSAGE = (
    "This account can't sign in with the selected role. Choose the other option and try again."
)
SESSION_EXPIRED_MESSAGE = "Your session has expired. Please log in again."
LOGOUT_SUCCESS_MESSAGE = "Logged out successfully."
LOGOUT_TOKEN_INVALID_MESSAGE = "This session has already ended or the token is invalid."
LOGOUT_WRONG_ACCOUNT_MESSAGE = "This refresh token belongs to a different account."
FORGOT_PASSWORD_MESSAGE = "If an account exists for this email, a password reset code has been sent."
RESET_CODE_LIFETIME = timedelta(minutes=10)
RESET_CODE_VERIFIED_MESSAGE = "Code verified. You can now set a new password."
RESET_TOKEN_INVALID_MESSAGE = "Your password reset has expired. Please request a new code."
PASSWORD_RESET_SUCCESS_MESSAGE = "Password reset successfully. Please log in with your new password."
PASSWORD_CHANGED_MESSAGE = "Password changed successfully."
CURRENT_PASSWORD_INCORRECT_MESSAGE = "Your current password is incorrect."
TOO_MANY_ATTEMPTS_TRY_LATER_MESSAGE = "Too many attempts. Please try again later."


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


class PasswordResetToken:
    """One-time proof that the user verified a reset code: "<base64 user id>:<Django reset token>".

    Django's token expires after PASSWORD_RESET_TIMEOUT and stops working once the password
    (or last_login) changes, so it can be used only once.
    """

    @staticmethod
    def issue(user) -> str:
        return f"{urlsafe_base64_encode(force_bytes(user.pk))}:{default_token_generator.make_token(user)}"

    @staticmethod
    def read(reset_token: str) -> User:
        uidb64, _, token = reset_token.partition(":")
        try:
            user = User.objects.filter(pk=force_str(urlsafe_base64_decode(uidb64)), is_active=True).first()
        except (ValueError, TypeError, OverflowError, DjangoValidationError):
            user = None
        if user is None or not default_token_generator.check_token(user, token):
            raise ValidationError({"error": RESET_TOKEN_INVALID_MESSAGE})
        return user


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


class InvalidCredentials(APIException):
    status_code = status.HTTP_401_UNAUTHORIZED

    def __init__(self):
        super().__init__()
        self.detail = {"error": INVALID_CREDENTIALS_MESSAGE}


class EmailNotVerified(APIException):
    status_code = status.HTTP_403_FORBIDDEN

    def __init__(self):
        super().__init__()
        self.detail = {"error": EMAIL_NOT_VERIFIED_MESSAGE}


NOT_ADMIN_MESSAGE = "This account can't sign in to the Admin Control Panel."


class NotAdminAccount(APIException):
    status_code = status.HTTP_403_FORBIDDEN

    def __init__(self):
        super().__init__()
        self.detail = {"error": NOT_ADMIN_MESSAGE, "code": "not_admin"}


class WrongSignInRole(APIException):
    status_code = status.HTTP_403_FORBIDDEN

    def __init__(self):
        super().__init__()
        self.detail = {"error": WRONG_SIGN_IN_ROLE_MESSAGE}


class InvalidRefreshToken(APIException):
    status_code = status.HTTP_401_UNAUTHORIZED

    def __init__(self):
        super().__init__()
        self.detail = {"error": SESSION_EXPIRED_MESSAGE}


class LogoutTokenInvalid(APIException):
    status_code = status.HTTP_400_BAD_REQUEST

    def __init__(self):
        super().__init__()
        self.detail = {"error": LOGOUT_TOKEN_INVALID_MESSAGE}


class LogoutWrongAccount(APIException):
    status_code = status.HTTP_403_FORBIDDEN

    def __init__(self):
        super().__init__()
        self.detail = {"error": LOGOUT_WRONG_ACCOUNT_MESSAGE}


def blacklist_all_refresh_tokens(user) -> None:
    """Logs the user out everywhere: none of their unexpired refresh tokens can be used again."""
    live = OutstandingToken.objects.filter(
        user=user, expires_at__gt=timezone.now(), blacklistedtoken__isnull=True
    )
    # One insert for all tokens; a row already added by a concurrent logout is skipped.
    BlacklistedToken.objects.bulk_create(
        [BlacklistedToken(token=outstanding) for outstanding in live], ignore_conflicts=True
    )


HOW_CHANGED = "changed from your account settings"
HOW_RESET = "reset with an emailed code"


def notify_password_changed(user, how: str, device_info: dict) -> None:
    """Emails the account owner that the password changed, once the change is saved.

    Never blocks the change: if the email cannot be queued, only a warning is logged.
    """
    when = timezone.now().strftime("%d %b %Y, %H:%M UTC")

    def queue():
        try:
            send_password_changed_email_task.delay(
                user.email, when, how, device_info.get("device", "Unknown device"), device_info.get("ip", "Unknown")
            )
        except OperationalError:
            logger.warning("Password-changed email could not be queued")

    transaction.on_commit(queue)


def stored_email_for(typed_email: str) -> str:
    """Returns the email as stored, because authenticate() matches it exactly (case-sensitive)."""
    if User.objects.filter(email=typed_email).exists():
        return typed_email
    matches = list(User.objects.filter(email__iexact=typed_email).values_list("email", flat=True)[:2])
    # Unknown or ambiguous: keep the typed email so authenticate() still runs its normal password work.
    return matches[0] if len(matches) == 1 else typed_email


class OTPService:
    """Manages generation, expiration, and validation of 6-digit OTP codes."""

    @staticmethod
    def send_verification_code(email: str):
        """Sends a sign-up code to an email with no verified account, at most once per cooldown."""
        if User.objects.filter(email__iexact=email, is_verified=True).exists():
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
        otp = OTPService._check_code(email, code, purpose="verification")
        return SignupVerificationToken.issue(otp.email)

    @staticmethod
    def verify_reset_code(email: str, code: str) -> str:
        """Checks a password-reset code and returns a one-time reset token."""
        otp = OTPService._check_code(email, code, purpose="password_reset")
        # The account may have been deactivated (or changed) after the code was sent.
        user = User.objects.filter(email=otp.email, is_verified=True, is_active=True).first()
        if user is None:
            raise CodeVerificationFailed({"error": NO_ACTIVE_CODE_MESSAGE})
        return PasswordResetToken.issue(user)

    @staticmethod
    def _check_code(email: str, code: str, purpose: str) -> OTP:
        """Uses up the latest matching code, or counts a wrong guess and raises a 400."""
        error = None
        with transaction.atomic():
            otp = (
                OTP.objects.select_for_update()
                .filter(email__iexact=email, purpose=purpose, is_used=False)
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
        return otp

    @staticmethod
    def generate(email: str, purpose="verification", lifetime=timedelta(minutes=2)):
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
            expires_at=timezone.now() + lifetime
        )

        # Signup emails keep the original two-argument call, so a worker still running older code
        # (for example during a deploy) can send them.
        extra = {} if purpose == "verification" else {"purpose": purpose}
        send_otp_email_task.delay(email, code, **extra)

        return code


class AuthService:
    """Manages user accounts, creation, login sessions, and password recovery."""

    @staticmethod
    def signup(validated_data: dict) -> User:
        """Creates a verified account for the email proven by the verification token.

        An unverified account left by the old signup flow is updated in place instead.
        """
        email = SignupVerificationToken.read(validated_data["verification_token"]).lower()

        if User.objects.filter(email__iexact=email, is_verified=True).exists():
            raise ValidationError({"error": ACCOUNT_EXISTS_MESSAGE})

        password = validated_data["password"]
        try:
            validate_password(password, user=User(email=email, full_name=validated_data["full_name"]))
        except DjangoValidationError as exc:
            raise ValidationError({"password": list(exc.messages)})

        organisation_name = validated_data["organization"].strip()
        profile = {
            "full_name": validated_data["full_name"],
            "department": validated_data["department"],
            "role": "",
            "account_type": User.SUPER_ADMIN,
            "is_verified": True,
        }
        try:
            with transaction.atomic():
                legacy = list(User.objects.select_for_update().filter(email__iexact=email))
                # Staff and superuser accounts are never taken over by signup, verified or not.
                if len(legacy) > 1 or any(u.is_verified or u.is_staff or u.is_superuser for u in legacy):
                    raise ValidationError({"error": ACCOUNT_EXISTS_MESSAGE})
                user = legacy[0] if legacy else None
                organisation = user.organisation if user else None
                # Only an organisation this account has to itself is renamed; a shared one is left alone.
                if organisation and organisation.users.exclude(pk=user.pk).exists():
                    organisation = None

                # A taken name is refused, never joined; the account's own current name is fine.
                taken = Organisation.objects.filter(name__iexact=organisation_name)
                if organisation:
                    taken = taken.exclude(pk=organisation.pk)
                if taken.exists():
                    raise ValidationError({"organization": [ORGANISATION_TAKEN_MESSAGE]})

                if organisation:
                    organisation.name = organisation_name
                    organisation.save(update_fields=["name"])
                else:
                    organisation = Organisation.objects.create(name=organisation_name)

                if user:
                    user.email = email
                    user.organisation = organisation
                    for field, value in profile.items():
                        setattr(user, field, value)
                    user.set_password(password)
                    user.save()
                    return user
                return User.objects.create_user(
                    username=uuid.uuid4().hex,
                    email=email,
                    password=password,
                    organisation=organisation,
                    **profile,
                )
        except IntegrityError as exc:
            # Two signups at once: a unique constraint stops the second, on the name or the email.
            if ORGANISATION_NAME_CONSTRAINT in str(exc):
                raise ValidationError({"organization": [ORGANISATION_TAKEN_MESSAGE]})
            raise ValidationError({"error": ACCOUNT_EXISTS_MESSAGE})

    @staticmethod
    def login(request, email: str, password: str, role: str) -> dict:
        """Checks email, password, and sign-in role, and returns JWT tokens with the user's profile."""
        stored_email = stored_email_for(email)
        user = authenticate(request, username=stored_email, password=password)
        if user is None:
            raise InvalidCredentials()
        # Checked only after the password is correct, so a guesser learns nothing.
        if not user.is_verified:
            raise EmailNotVerified()
        # One message for every mismatch, so it never names the account's real type.
        if role != user.account_type:
            raise WrongSignInRole()

        refresh = RefreshToken.for_user(user)
        update_last_login(None, user)
        return {
            "access": str(refresh.access_token),
            "refresh": str(refresh),
            "user": {
                "id": str(user.id),
                "email": user.email,
                "full_name": user.full_name,
                "organization": user.organisation.name if user.organisation else "",
                "department": user.department,
                "role": user.role,
                "account_type": user.account_type,
                "must_change_password": user.must_change_password,
                "profile_picture": user.profile_picture_url or None,
            },
        }

    @staticmethod
    def admin_login(request, email: str, password: str) -> dict:
        """Admin Control Panel sign-in: AxiomVault staff only. Returns JWT tokens and the admin role.

        `is_verified` is not checked: staff accounts come from `createsuperuser` or the owner, never signup.
        """
        user = authenticate(request, username=stored_email_for(email), password=password)
        if user is None:
            raise InvalidCredentials()
        # Checked only after the password is correct, like the app login's role check.
        if not user.is_staff:
            raise NotAdminAccount()

        refresh = RefreshToken.for_user(user)
        update_last_login(None, user)
        return {
            "access": str(refresh.access_token),
            "refresh": str(refresh),
            "user": {
                "id": str(user.id),
                "email": user.email,
                "full_name": user.full_name,
                "admin_role": "owner" if user.is_superuser else "staff",
                "must_change_password": user.must_change_password,
                "profile_picture": user.profile_picture_url or None,
            },
        }

    @staticmethod
    def refresh_access_token(refresh: str) -> dict:
        """Trades a valid refresh token for a new access token. The refresh token is not rotated."""
        try:
            # Rejects expired, tampered, malformed, and access (wrong type) tokens.
            token = RefreshToken(refresh)
        except TokenError:
            raise InvalidRefreshToken()

        user_id = token.payload.get(jwt_settings.USER_ID_CLAIM)
        # filter().first() returns None for a deleted user instead of raising (simplejwt's own view 500s).
        user = User.objects.filter(**{jwt_settings.USER_ID_FIELD: user_id}).first() if user_id else None
        if user is None or not user.is_active:
            raise InvalidRefreshToken()

        return {"access": str(token.access_token)}

    @staticmethod
    def forgot_password(email: str) -> None:
        """Emails a reset code to a verified, active account. Returns the same way for every
        email, so the caller can always answer FORGOT_PASSWORD_MESSAGE without revealing accounts."""
        matches = list(User.objects.filter(email__iexact=email, is_verified=True, is_active=True)[:2])
        if len(matches) != 1:
            return
        user = matches[0]

        latest = (
            OTP.objects.filter(email__iexact=user.email, purpose="password_reset")
            .order_by("-created_at")
            .first()
        )
        if latest and (timezone.now() - latest.created_at).total_seconds() < VERIFICATION_CODE_COOLDOWN_SECONDS:
            # Silent cooldown: a 429 here would reveal that the account exists.
            return

        try:
            with transaction.atomic():
                OTPService.generate(user.email, purpose="password_reset", lifetime=RESET_CODE_LIFETIME)
        except OperationalError:
            raise CodeDeliveryUnavailable()

    @staticmethod
    def change_password(user, current_password: str, new_password: str, device_info: dict) -> dict:
        """Changes a logged-in user's password, ends every session, and returns fresh tokens
        for the device that made the change."""
        # Checked first, so a caller without the current password learns nothing about the rules.
        if not user.check_password(current_password):
            raise ValidationError({"current_password": [CURRENT_PASSWORD_INCORRECT_MESSAGE]})
        try:
            validate_password(new_password, user=user)
        except DjangoValidationError as e:
            raise ValidationError({"new_password": list(e.messages)})

        with transaction.atomic():
            user.set_password(new_password)
            user.must_change_password = False
            user.save(update_fields=["password", "must_change_password"])
            blacklist_all_refresh_tokens(user)
            refresh = RefreshToken.for_user(user)
            notify_password_changed(user, HOW_CHANGED, device_info)

        return {
            "message": PASSWORD_CHANGED_MESSAGE,
            "access": str(refresh.access_token),
            "refresh": str(refresh),
        }

    @staticmethod
    def reset_password(reset_token: str, new_password: str, device_info: dict | None = None) -> User:
        """Sets the new password for the reset token's user and ends all of their sessions."""
        user = PasswordResetToken.read(reset_token)
        try:
            validate_password(new_password, user=user)
        except DjangoValidationError as e:
            raise ValidationError({"new_password": list(e.messages)})

        with transaction.atomic():
            user.set_password(new_password)
            user.must_change_password = False
            user.save(update_fields=["password", "must_change_password"])
            OTP.objects.filter(email__iexact=user.email, purpose="password_reset", is_used=False).update(
                is_used=True
            )
            blacklist_all_refresh_tokens(user)
            notify_password_changed(user, HOW_RESET, device_info or {})
        return user

    @staticmethod
    def logout(user, refresh: str) -> None:
        """Blacklists the caller's refresh token so it can no longer be used."""
        try:
            # Also rejects a token that is already blacklisted (logged out before).
            token = RefreshToken(refresh)
        except TokenError:
            raise LogoutTokenInvalid()

        if str(token.payload.get(jwt_settings.USER_ID_CLAIM)) != str(user.pk):
            raise LogoutWrongAccount()

        token.blacklist()


class TeamService:
    """A super admin managing the team members of their own organisation."""

    @staticmethod
    def members_of(super_admin):
        """Every team member in the super admin's organisation, newest first. Nothing else is reachable."""
        return User.objects.filter(
            organisation=super_admin.organisation, account_type=User.TEAM_MEMBER
        ).order_by("-date_joined")

    @staticmethod
    def add_member(super_admin, data: dict) -> User:
        """Creates a verified team member with a temporary password they must change on first login."""
        full_name = data["full_name"].strip()
        try:
            validate_password(data["password"], user=User(email=data["email"], full_name=full_name))
        except DjangoValidationError as exc:
            raise ValidationError({"password": list(exc.messages)})
        try:
            with transaction.atomic():
                return User.objects.create_user(
                    username=uuid.uuid4().hex,
                    email=data["email"],
                    password=data["password"],
                    full_name=full_name,
                    phone_number=data["phone_number"],
                    role=data["role"].strip(),
                    department="",
                    organisation=super_admin.organisation,
                    account_type=User.TEAM_MEMBER,
                    is_verified=True,
                    must_change_password=True,
                )
        except IntegrityError:
            # Two requests adding the same email at once: the unique email index stops the second.
            raise ValidationError({"email": [EMAIL_TAKEN_MESSAGE]})

    @staticmethod
    def update_member(member: User, data: dict) -> User:
        """Changes only the fields that were sent."""
        for field in ("full_name", "role"):
            if field in data:
                data[field] = data[field].strip()
        for field, value in data.items():
            setattr(member, field, value)
        try:
            with transaction.atomic():
                member.save(update_fields=list(data) or None)
        except IntegrityError:
            raise ValidationError({"email": [EMAIL_TAKEN_MESSAGE]})
        return member

    @staticmethod
    def set_active(member: User, active: bool) -> User:
        """Deactivating also ends every session at once; both directions are safe to repeat."""
        with transaction.atomic():
            member.is_active = active
            member.save(update_fields=["is_active"])
            if not active:
                blacklist_all_refresh_tokens(member)
        return member
