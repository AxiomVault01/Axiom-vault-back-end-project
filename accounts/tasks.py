from celery import shared_task
from django.core.mail import send_mail
from django.conf import settings


@shared_task(bind=True)
def send_otp_email_task(self, email, code):
    print(f"EMAIL TASK STARTED -> {email}")
    print(f"OTP -> {code}")

    try:
        result = send_mail(
            subject="Your AxiomVault Verification Code",
            message=f"Your OTP is {code}",
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[email],
            fail_silently=False,
        )

        print(f"SEND_MAIL RESULT -> {result}")
        print(f"EMAIL SENT -> {email}")

    except Exception as e:
        print(f"EMAIL ERROR -> {str(e)}")
        raise

    return True