from datetime import timedelta
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.core import mail
from django.utils import timezone
from kombu.exceptions import OperationalError
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIClient

from accounts.models import OTP
from accounts.services import (
    ACCOUNT_EXISTS_MESSAGE,
    CodeDeliveryUnavailable,
    CodeRequestTooSoon,
    OTPService,
)

User = get_user_model()

NEW_EMAIL = "auditor@agency.gov"


def create_user(email="ada@example.com"):
    return User.objects.create_user(
        username=email.split("@")[0],
        email=email,
        password="Str0ng-Passw0rd!",
        full_name="Ada Obi",
        organization="Axiom",
        department="operations",
        role="fraud_analyst",
    )


def age_codes(email, seconds):
    OTP.objects.filter(email=email).update(created_at=timezone.now() - timedelta(seconds=seconds))


@pytest.mark.django_db
def test_new_user_is_unverified_and_password_is_hashed():
    user = create_user()

    assert user.is_verified is False
    assert user.password != "Str0ng-Passw0rd!"
    assert user.check_password("Str0ng-Passw0rd!")


@pytest.mark.django_db
def test_send_verification_code_creates_code_and_emails_it():
    OTPService.send_verification_code(NEW_EMAIL)

    otp = OTP.objects.get(email=NEW_EMAIL)
    assert otp.purpose == "verification"
    assert otp.is_used is False
    assert len(otp.code) == 6 and otp.code.isdigit()
    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == [NEW_EMAIL]
    assert otp.code in mail.outbox[0].body


@pytest.mark.django_db
def test_send_verification_code_rejects_existing_account_in_any_letter_case():
    create_user("ada@example.com")

    with pytest.raises(ValidationError) as excinfo:
        OTPService.send_verification_code("ADA@Example.com")

    assert excinfo.value.detail == {"error": ACCOUNT_EXISTS_MESSAGE}
    assert OTP.objects.count() == 0
    assert mail.outbox == []


@pytest.mark.django_db
def test_second_request_within_cooldown_is_refused():
    OTPService.send_verification_code(NEW_EMAIL)
    age_codes(NEW_EMAIL, 18)

    with pytest.raises(CodeRequestTooSoon) as excinfo:
        OTPService.send_verification_code(NEW_EMAIL)

    assert excinfo.value.status_code == 429
    assert excinfo.value.detail["retry_after"] == 42
    assert OTP.objects.count() == 1


@pytest.mark.django_db
def test_request_after_cooldown_sends_new_code_and_retires_old_one():
    OTPService.send_verification_code(NEW_EMAIL)
    age_codes(NEW_EMAIL, 61)

    OTPService.send_verification_code(NEW_EMAIL)

    codes = OTP.objects.filter(email=NEW_EMAIL).order_by("created_at")
    assert [otp.is_used for otp in codes] == [True, False]
    assert len(mail.outbox) == 2


@pytest.mark.django_db
def test_broker_failure_leaves_no_new_code_and_keeps_old_one_valid():
    OTPService.send_verification_code(NEW_EMAIL)
    age_codes(NEW_EMAIL, 61)

    with patch("accounts.services.send_otp_email_task.delay", side_effect=OperationalError("broker down")):
        with pytest.raises(CodeDeliveryUnavailable) as excinfo:
            OTPService.send_verification_code(NEW_EMAIL)

    assert excinfo.value.status_code == 503
    assert OTP.objects.count() == 1
    assert OTP.objects.get().is_used is False


SEND_OTP_URL = "/api/v1/auth/send-otp/"


@pytest.fixture
def api_client():
    return APIClient()


@pytest.mark.django_db
def test_send_otp_endpoint_returns_200_and_emails_code(api_client):
    response = api_client.post(SEND_OTP_URL, {"email": NEW_EMAIL}, format="json")

    assert response.status_code == 200
    assert response.json() == {"message": "OTP verification code transmitted successfully."}
    assert len(mail.outbox) == 1


@pytest.mark.django_db
def test_send_otp_endpoint_works_with_debug_off(api_client, settings):
    settings.DEBUG = False

    response = api_client.post(SEND_OTP_URL, {"email": NEW_EMAIL}, format="json")

    assert response.status_code == 200
    assert OTP.objects.filter(email=NEW_EMAIL).count() == 1


@pytest.mark.django_db
@pytest.mark.parametrize("body", [{}, {"email": "not-an-email"}])
def test_send_otp_endpoint_rejects_missing_or_invalid_email(api_client, body):
    response = api_client.post(SEND_OTP_URL, body, format="json")

    assert response.status_code == 400
    assert "email" in response.json()
    assert OTP.objects.count() == 0


@pytest.mark.django_db
def test_send_otp_endpoint_rejects_existing_account(api_client):
    create_user("ada@example.com")

    response = api_client.post(SEND_OTP_URL, {"email": "ada@example.com"}, format="json")

    assert response.status_code == 400
    assert response.json() == {"error": ACCOUNT_EXISTS_MESSAGE}


@pytest.mark.django_db
def test_send_otp_endpoint_returns_429_with_retry_after(api_client):
    api_client.post(SEND_OTP_URL, {"email": NEW_EMAIL}, format="json")

    response = api_client.post(SEND_OTP_URL, {"email": NEW_EMAIL}, format="json")

    assert response.status_code == 429
    body = response.json()
    assert body["error"] == "Please wait before requesting a new code."
    assert 1 <= body["retry_after"] <= 60


@pytest.mark.django_db
def test_send_otp_endpoint_returns_503_when_email_cannot_be_queued(api_client):
    with patch("accounts.services.send_otp_email_task.delay", side_effect=OperationalError("broker down")):
        response = api_client.post(SEND_OTP_URL, {"email": NEW_EMAIL}, format="json")

    assert response.status_code == 503
    assert response.json() == {"error": "We could not send the verification code. Please try again shortly."}
    assert OTP.objects.count() == 0
