import logging

from celery import shared_task
from django.core.mail import send_mail
from django.conf import settings

logger = logging.getLogger(__name__)


class OTPEmailDeliveryError(Exception):
    """Raised instead of the SMTP error, whose text can contain the recipient address."""


@shared_task(bind=True)
def send_otp_email_task(self, email, code):
    try:
        send_mail(
            subject="Your AxiomVault Verification Code",
            message=f"Your OTP is {code}",
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
