import logging

from celery import shared_task
from django.core.mail import send_mail
from django.conf import settings

logger = logging.getLogger(__name__)


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
    except Exception:
        logger.exception("OTP email could not be sent")
        raise

    logger.info("OTP email sent")
    return True
