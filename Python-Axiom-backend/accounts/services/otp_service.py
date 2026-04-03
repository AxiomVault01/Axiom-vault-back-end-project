import random
from datetime import timedelta
from django.utils import timezone

from accounts.models import OTP
from accounts.tasks import send_otp_email_task


class OTPService:

    @staticmethod
    def generate(user, otp_type="signup"):
        code = str(random.randint(1000, 9999))

        # ✅ Invalidate old OTPs
        OTP.objects.filter(
            user=user,
            type=otp_type,
            is_used=False
        ).update(is_used=True)

        # ✅ Create new OTP
        OTP.objects.create(
            user=user,
            code=code,
            type=otp_type,
            expires_at=timezone.now() + timedelta(minutes=5),
        )

        # ✅ Send asynchronously
        send_otp_email_task.delay(user.email, code)

        return code

    @staticmethod
    def verify(user, code, otp_type):
        otp = OTP.objects.filter(
            user=user,
            code=code,
            type=otp_type,
            is_used=False
        ).last()

        if not otp:
            return False, "Invalid OTP"

        if otp.attempt_count >= 5:
            return False, "Too many attempts"

        if otp.expires_at < timezone.now():
            return False, "Expired OTP"

        # ✅ increment attempts
        otp.attempt_count += 1

        # ✅ mark used if correct
        if otp.code == code:
            otp.is_used = True

        otp.save()

        return True, None