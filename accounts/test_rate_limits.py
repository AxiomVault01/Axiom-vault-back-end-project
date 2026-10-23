import pytest
from rest_framework.test import APIClient

from accounts.models import AuditLog
from accounts.services import CODE_TOO_SOON_MESSAGE, TOO_MANY_ATTEMPTS_TRY_LATER_MESSAGE
from accounts.tests import create_user

AUTH = "/api/v1/auth/"
ADMIN_LOGIN = "/api/v1/admin/auth/login/"


def post(path, body, ip="10.0.0.1", client=None):
    """POSTs as if from `ip`, the way Render's proxy reports the caller (last X-Forwarded-For entry)."""
    return (client or APIClient()).post(path, body, format="json", HTTP_X_FORWARDED_FOR=f"203.0.113.9, {ip}")


def wrong_login(email="ada@example.com", ip="10.0.0.1", path=AUTH + "login/"):
    return post(path, {"email": email, "password": "wrong", "role": "super_admin"}, ip=ip)


def assert_limited(response):
    assert response.status_code == 429
    body = response.json()
    assert body["error"] == TOO_MANY_ATTEMPTS_TRY_LATER_MESSAGE
    assert body["retry_after"] > 0
    assert int(response["Retry-After"]) > 0


@pytest.mark.django_db
def test_login_allows_ten_a_minute_per_ip():
    create_user("ada@example.com")
    emails = [f"user{n}@example.com" for n in range(10)]  # Different emails, so only the IP limit counts.

    statuses = [wrong_login(email).status_code for email in emails]
    blocked = wrong_login("next@example.com")

    assert statuses == [401] * 10
    assert_limited(blocked)
    assert blocked.json()["retry_after"] <= 60
    assert wrong_login("next@example.com", ip="10.0.0.2").status_code == 401


@pytest.mark.django_db
def test_login_email_limit_holds_across_ips_and_panels():
    create_user("ada@example.com")
    for n in range(5):
        assert wrong_login(ip=f"10.0.1.{n}").status_code == 401
    for n in range(5):
        assert wrong_login(ip=f"10.0.2.{n}", path=ADMIN_LOGIN).status_code == 401

    blocked = wrong_login(ip="10.0.3.1")

    assert_limited(blocked)
    assert 60 < blocked.json()["retry_after"] <= 15 * 60
    assert_limited(wrong_login("ADA@example.com ", ip="10.0.3.2", path=ADMIN_LOGIN))
    assert wrong_login("other@example.com", ip="10.0.3.3").status_code == 401


@pytest.mark.django_db
def test_a_blocked_login_is_not_checked_or_recorded():
    create_user("ada@example.com")
    for _ in range(10):
        wrong_login()

    response = post(AUTH + "login/", {"email": "ada@example.com", "password": "Str0ng-Passw0rd!", "role": "super_admin"})

    assert_limited(response)
    assert not AuditLog.objects.filter(action=AuditLog.LOGIN).exists()


@pytest.mark.django_db
@pytest.mark.parametrize("path, body, limit", [
    ("token/refresh/", {"refresh": "not-a-token"}, 30),
    ("signup/", {"signup_token": "bad", "full_name": "A", "organisation_name": "B", "password": "x", "confirm_password": "x"}, 10),
    ("verify-otp/", {"email": "ada@example.com", "code": "000000"}, 20),
    ("verify-reset-code/", {"email": "ada@example.com", "code": "000000"}, 20),
    ("reset-password/", {"reset_token": "bad", "new_password": "x", "re_enter_password": "x"}, 20),
])
def test_each_endpoint_has_its_ip_limit(path, body, limit):
    statuses = {post(AUTH + path, body).status_code for _ in range(limit)}

    assert 429 not in statuses
    assert_limited(post(AUTH + path, body))
    assert post(AUTH + path, body, ip="10.9.9.9").status_code != 429


@pytest.mark.django_db
def test_code_checks_share_one_counter():
    body = {"email": "ada@example.com", "code": "000000"}
    for _ in range(10):
        post(AUTH + "verify-otp/", body)
    for _ in range(10):
        post(AUTH + "verify-reset-code/", body)

    assert_limited(post(AUTH + "reset-password/", {"reset_token": "bad", "new_password": "x", "re_enter_password": "x"}))


@pytest.mark.django_db
def test_code_requests_share_an_hourly_ip_limit():
    for n in range(4):
        assert post(AUTH + "send-otp/", {"email": f"new{n}@example.com"}).status_code == 200
    for n in range(3):
        assert post(AUTH + "resend-otp/", {"email": f"new{n}@example.com"}).status_code == 429  # 60-second cooldown
    for n in range(3):
        post(AUTH + "forgot-password/", {"email": f"nobody{n}@example.com"})

    blocked = post(AUTH + "send-otp/", {"email": "fresh@example.com"})

    assert_limited(blocked)
    assert 60 < blocked.json()["retry_after"] <= 60 * 60


@pytest.mark.django_db
def test_code_cooldown_is_unchanged_under_the_limit():
    post(AUTH + "send-otp/", {"email": "new@example.com"})

    again = post(AUTH + "send-otp/", {"email": "new@example.com"})

    assert again.status_code == 429
    assert again.json()["error"] == CODE_TOO_SOON_MESSAGE


@pytest.mark.django_db
def test_without_a_proxy_header_the_connection_ip_counts():
    client = APIClient()
    for n in range(10):
        client.post(AUTH + "login/", {"email": f"u{n}@example.com", "password": "x", "role": "super_admin"}, format="json")

    response = client.post(AUTH + "login/", {"email": "z@example.com", "password": "x", "role": "super_admin"}, format="json")

    assert_limited(response)


@pytest.mark.django_db
def test_a_body_without_email_is_limited_by_ip_only():
    statuses = [post(AUTH + "login/", {"password": "x", "role": "super_admin"}, ip=f"10.0.5.{n}").status_code for n in range(12)]

    assert statuses == [400] * 12
