import logging

from celery import shared_task
from django.core.mail import send_mail
from django.conf import settings

logger = logging.getLogger(__name__)


class OTPEmailDeliveryError(Exception):
    """Raised instead of the SMTP error, whose text can contain the recipient address."""


PASSWORD_RESET_EMAIL_SUBJECT = "Your AxiomVault password reset code"


def otp_email_content(code, purpose):
    """Returns (subject, body) for the code email; signup emails keep their original text."""
    if purpose == "password_reset":
        return PASSWORD_RESET_EMAIL_SUBJECT, (
            f"Your password reset code is {code}.\n\n"
            "It expires in 10 minutes.\n\n"
            "If you did not ask to reset your password, ignore this email; your password stays the same."
        )
    return "Your AxiomVault Verification Code", f"Your OTP is {code}"


@shared_task(bind=True)
def send_otp_email_task(self, email, code, purpose="verification"):
    subject, message = otp_email_content(code, purpose)
    try:
        send_mail(
            subject=subject,
            message=message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[email],
            fail_silently=False,
        )
    except Exception as exc:
        error_type = type(exc).__name__
        logger.error(
            "OTP email could not be sent: %s (SMTP code %s)",
            error_type,
            getattr(exc, "smtp_code", "n/a"),
        )
        raise OTPEmailDeliveryError(f"OTP email delivery failed: {error_type}") from None

    logger.info("OTP email sent")
    return True


PASSWORD_CHANGED_EMAIL_SUBJECT = "Your AxiomVault password was changed"


def password_changed_email_body(when, how, device, ip):
    return (
        f"Your AxiomVault password was {how}.\n\n"
        f"When: {when}\n"
        f"Device (as reported by the device): {device}\n"
        f"IP address: {ip}\n\n"
        "If this was you, you don't need to do anything.\n\n"
        "If this wasn't you, reset your password now using Forgot password on the sign-in "
        "screen, and contact your administrator."
    )


@shared_task(bind=True)
def send_password_changed_email_task(self, email, when, how, device, ip):
    try:
        send_mail(
            subject=PASSWORD_CHANGED_EMAIL_SUBJECT,
            message=password_changed_email_body(when, how, device, ip),
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[email],
            fail_silently=False,
        )
    except Exception as exc:
        error_type = type(exc).__name__
        logger.error(
            "Password-changed email could not be sent: %s (SMTP code %s)",
            error_type,
            getattr(exc, "smtp_code", "n/a"),
        )
        raise OTPEmailDeliveryError(f"Password-changed email delivery failed: {error_type}") from None

    logger.info("Password-changed email sent")
    return True
