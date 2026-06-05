from celery import shared_task
from django.core.mail import send_mail
from django.conf import settings


@shared_task(bind=True, autoretry_for=(Exception,), retry_backoff=5, retry_kwargs={"max_retries": 3})
def send_otp_email_task(self, email, code):
    """
    Sends OTP email asynchronously.
    Retries automatically if email fails.
    """

    subject = "Your AxiomVault Verification Code"

    message = f"""
Hello,

Your One-Time Password (OTP) is: {code}

This code will expire in 5 minutes.

If you did not request this, please ignore this email.

— AxiomVault Security Team
"""

    try:
        send_mail(
            subject=subject,
            message=message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[email],
            fail_silently=False,
        )

        return f"OTP sent to {email}"

    except Exception as e:
        raise self.retry(exc=e)