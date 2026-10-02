from rest_framework.throttling import UserRateThrottle

CHANGE_PASSWORD_ATTEMPTS = 5
CHANGE_PASSWORD_WINDOW_SECONDS = 15 * 60


class ChangePasswordRateThrottle(UserRateThrottle):
    """At most 5 change-password attempts per user every 15 minutes, counted in the shared cache.

    DRF's "5/min"-style rate strings cannot express 15 minutes, so the rate is set directly.
    """

    scope = "change_password"

    def get_rate(self):
        return f"{CHANGE_PASSWORD_ATTEMPTS}/{CHANGE_PASSWORD_WINDOW_SECONDS}s"

    def parse_rate(self, rate):
        return CHANGE_PASSWORD_ATTEMPTS, CHANGE_PASSWORD_WINDOW_SECONDS
