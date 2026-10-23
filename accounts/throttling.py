"""Rate limits for the auth endpoints (feature 11) and change password. Counts live in the shared cache."""

import math

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiExample, OpenApiResponse
from rest_framework.exceptions import Throttled
from rest_framework.throttling import SimpleRateThrottle, UserRateThrottle

from .device import client_ip
from .services import TOO_MANY_ATTEMPTS_TRY_LATER_MESSAGE

CHANGE_PASSWORD_ATTEMPTS = 5
CHANGE_PASSWORD_WINDOW_SECONDS = 15 * 60

# scope: (requests, seconds). DRF's "5/min" strings cannot say "15 minutes", so rates are set here.
RATES = {
    "login": (10, 60),
    "login_email": (10, 15 * 60),
    "token_refresh": (30, 60),
    "signup": (10, 60 * 60),
    "code_request": (10, 60 * 60),
    "code_check": (20, 15 * 60),
}

# The same limits in words, for Swagger.
LOGIN_LIMIT = "10 attempts a minute per IP address, and 10 every 15 minutes per email, app and admin sign-in together"
REFRESH_LIMIT = "30 a minute per IP address"
SIGNUP_LIMIT = "10 an hour per IP address"
CODE_REQUEST_LIMIT = "10 code requests an hour per IP address, across send-otp, resend-otp, and forgot-password"
CODE_CHECK_LIMIT = "20 attempts every 15 minutes per IP address, across verify-otp, verify-reset-code, and reset-password"


class ChangePasswordRateThrottle(UserRateThrottle):
    """At most 5 change-password attempts per user every 15 minutes."""

    scope = "change_password"

    def get_rate(self):
        return f"{CHANGE_PASSWORD_ATTEMPTS}/{CHANGE_PASSWORD_WINDOW_SECONDS}s"

    def parse_rate(self, rate):
        return CHANGE_PASSWORD_ATTEMPTS, CHANGE_PASSWORD_WINDOW_SECONDS


class _ScopedRateThrottle(SimpleRateThrottle):
    def __init__(self, scope):
        self.scope = scope
        super().__init__()

    def get_rate(self):
        return self.scope

    def parse_rate(self, rate):
        return RATES[rate]


class IPRateThrottle(_ScopedRateThrottle):
    """Counts requests per caller IP, read the same way as everywhere else (Render's proxy header)."""

    def get_cache_key(self, request, view):
        return self.cache_format % {"scope": self.scope, "ident": client_ip(request.META)}


class EmailRateThrottle(_ScopedRateThrottle):
    """Counts requests per email in the body, so guesses spread over many IPs still add up."""

    def get_cache_key(self, request, view):
        email = request.data.get("email") if hasattr(request.data, "get") else None
        if not isinstance(email, str) or not email.strip():
            return None  # No email: the IP limit still applies.
        return self.cache_format % {"scope": self.scope, "ident": email.strip().lower()}


# Which limits apply to which view action. Actions that share a scope share one counter.
ACTION_THROTTLES = {
    "login": lambda: [IPRateThrottle("login"), EmailRateThrottle("login_email")],
    "token_refresh": lambda: [IPRateThrottle("token_refresh")],
    "signup": lambda: [IPRateThrottle("signup")],
    "send_otp": lambda: [IPRateThrottle("code_request")],
    "resend_otp": lambda: [IPRateThrottle("code_request")],
    "forgot_password": lambda: [IPRateThrottle("code_request")],
    "verify_otp": lambda: [IPRateThrottle("code_check")],
    "verify_reset_code": lambda: [IPRateThrottle("code_check")],
    "reset_password": lambda: [IPRateThrottle("code_check")],
    "change_password": lambda: [ChangePasswordRateThrottle()],
}


TOO_MANY_EXAMPLE = OpenApiExample(
    "Too many attempts", value={"error": TOO_MANY_ATTEMPTS_TRY_LATER_MESSAGE, "retry_after": 42}
)


def rate_limited(limit):
    """Swagger 429 for a rate-limited endpoint; `limit` says the limit in words."""
    return OpenApiResponse(
        response=OpenApiTypes.OBJECT,
        description=(
            f"Rate limit reached ({limit}). Nothing was checked or changed. Wait `retry_after` seconds (also in the "
            "`Retry-After` header) and show a countdown."
        ),
        examples=[TOO_MANY_EXAMPLE],
    )


class AuthRateLimitMixin:
    """Applies ACTION_THROTTLES and answers 429 as `{error, retry_after}` plus the Retry-After header."""

    def get_throttles(self):
        throttles = ACTION_THROTTLES.get(getattr(self, "action", None))
        return throttles() if throttles else super().get_throttles()

    def throttled(self, request, wait):
        exc = Throttled(wait)
        exc.detail = {"error": TOO_MANY_ATTEMPTS_TRY_LATER_MESSAGE, "retry_after": math.ceil(wait or 0)}
        raise exc
