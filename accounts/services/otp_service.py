import random

from django.utils import timezone
from datetime import timedelta

from accounts.models import OTP
from accounts.tasks import send_otp_email_task


class OTPService:

    @staticmethod
    def generate(email):

        OTP.objects.filter(
            email=email,
            is_used=False
        ).delete()

        code = str(random.randint(100000, 999999))

        otp = OTP.objects.create(
            email=email,
            code=code,
            expires_at=timezone.now() + timedelta(minutes=2)
        )

        send_otp_email_task.delay(
            email=email,
            code=code
        )

        return otp

        @staticmethod
    def verify(email, code):

        try:
            otp = OTP.objects.filter(
                email=email,
                code=code,
                is_used=False
            ).latest("created_at")

        except OTP.DoesNotExist:
            return False, "Invalid OTP"

        if otp.is_expired:
            return False, "OTP expired"

        otp.is_used = True
        otp.save()

        return True, None