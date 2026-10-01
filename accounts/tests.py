import time
from datetime import timedelta
from smtplib import SMTPRecipientsRefused
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
    CODE_EXPIRED_MESSAGE,
    INCORRECT_CODE_MESSAGE,
    NO_ACTIVE_CODE_MESSAGE,
    SIGNUP_SUCCESS_MESSAGE,
    TOKEN_INVALID_MESSAGE,
    TOO_MANY_ATTEMPTS_MESSAGE,
    CodeDeliveryUnavailable,
    CodeRequestTooSoon,
    AuthService,
    CodeVerificationFailed,
    OTPService,
    SignupVerificationToken,
)
from accounts.tasks import OTPEmailDeliveryError, send_otp_email_task

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


@pytest.mark.django_db
def test_cooldown_applies_to_letter_case_variants_of_the_same_email():
    OTPService.send_verification_code("victim@example.com")

    with pytest.raises(CodeRequestTooSoon):
        OTPService.send_verification_code("VICTIM@Example.com")

    assert OTP.objects.count() == 1
    assert len(mail.outbox) == 1


def test_failed_otp_email_never_exposes_the_recipient_address(caplog):
    address = "victim@example.com"
    refused = SMTPRecipientsRefused({address: (550, b"5.1.1 victim@example.com does not exist")})

    with patch("accounts.tasks.send_mail", side_effect=refused):
        with pytest.raises(OTPEmailDeliveryError) as excinfo:
            send_otp_email_task.run(address, "123456")

    assert address not in str(excinfo.value)
    assert excinfo.value.__cause__ is None
    assert excinfo.value.__suppress_context__ is True
    assert "SMTPRecipientsRefused" in caplog.text
    assert address not in caplog.text
    assert "123456" not in caplog.text


def send_code(email=NEW_EMAIL):
    OTPService.send_verification_code(email)
    return OTP.objects.filter(email=email).latest("created_at")


def wrong_code(otp):
    return "000000" if otp.code != "000000" else "111111"


@pytest.mark.django_db
def test_correct_code_returns_token_for_the_email_and_uses_the_code():
    otp = send_code()

    token = OTPService.verify_signup_code(NEW_EMAIL, otp.code)

    assert SignupVerificationToken.read(token) == NEW_EMAIL
    otp.refresh_from_db()
    assert otp.is_used is True


@pytest.mark.django_db
def test_code_verifies_with_email_in_different_letter_case():
    otp = send_code()

    token = OTPService.verify_signup_code(NEW_EMAIL.upper(), otp.code)

    assert SignupVerificationToken.read(token) == NEW_EMAIL


@pytest.mark.django_db
def test_wrong_codes_count_down_then_cancel_the_code():
    otp = send_code()
    bad = wrong_code(otp)

    for remaining in (2, 1):
        with pytest.raises(CodeVerificationFailed) as excinfo:
            OTPService.verify_signup_code(NEW_EMAIL, bad)
        assert excinfo.value.detail == {"error": INCORRECT_CODE_MESSAGE, "attempts_remaining": remaining}

    with pytest.raises(CodeVerificationFailed) as excinfo:
        OTPService.verify_signup_code(NEW_EMAIL, bad)
    assert excinfo.value.detail == {"error": TOO_MANY_ATTEMPTS_MESSAGE}

    otp.refresh_from_db()
    assert otp.failed_attempts == 3
    assert otp.is_used is True
    with pytest.raises(CodeVerificationFailed) as excinfo:
        OTPService.verify_signup_code(NEW_EMAIL, otp.code)
    assert excinfo.value.detail == {"error": NO_ACTIVE_CODE_MESSAGE}


@pytest.mark.django_db
def test_expired_code_is_refused_and_cancelled():
    otp = send_code()
    OTP.objects.filter(pk=otp.pk).update(expires_at=timezone.now() - timedelta(seconds=1))

    with pytest.raises(CodeVerificationFailed) as excinfo:
        OTPService.verify_signup_code(NEW_EMAIL, otp.code)

    assert excinfo.value.detail == {"error": CODE_EXPIRED_MESSAGE}
    otp.refresh_from_db()
    assert otp.is_used is True


@pytest.mark.django_db
def test_verify_without_any_code_sent_is_refused():
    with pytest.raises(CodeVerificationFailed) as excinfo:
        OTPService.verify_signup_code(NEW_EMAIL, "123456")

    assert excinfo.value.detail == {"error": NO_ACTIVE_CODE_MESSAGE}


@pytest.mark.django_db
def test_used_code_cannot_be_reused():
    otp = send_code()
    OTPService.verify_signup_code(NEW_EMAIL, otp.code)

    with pytest.raises(CodeVerificationFailed) as excinfo:
        OTPService.verify_signup_code(NEW_EMAIL, otp.code)

    assert excinfo.value.detail == {"error": NO_ACTIVE_CODE_MESSAGE}


def test_tampered_token_is_rejected():
    token = SignupVerificationToken.issue(NEW_EMAIL)
    tampered = token.replace(NEW_EMAIL, "attacker@evil.test")

    with pytest.raises(ValidationError) as excinfo:
        SignupVerificationToken.read(tampered)

    assert excinfo.value.detail == {"error": TOKEN_INVALID_MESSAGE}


def test_token_expires_after_30_minutes():
    token = SignupVerificationToken.issue(NEW_EMAIL)
    now = time.time()

    with patch("django.core.signing.time.time", return_value=now + 29 * 60):
        assert SignupVerificationToken.read(token) == NEW_EMAIL
    with patch("django.core.signing.time.time", return_value=now + 31 * 60):
        with pytest.raises(ValidationError) as excinfo:
            SignupVerificationToken.read(token)

    assert excinfo.value.detail == {"error": TOKEN_INVALID_MESSAGE}


VERIFY_OTP_URL = "/api/v1/auth/verify-otp/"
RESEND_OTP_URL = "/api/v1/auth/resend-otp/"


@pytest.mark.django_db
def test_verify_otp_endpoint_returns_token(api_client):
    otp = send_code()

    response = api_client.post(VERIFY_OTP_URL, {"email": NEW_EMAIL, "code": otp.code}, format="json")

    assert response.status_code == 200
    body = response.json()
    assert body["message"] == "OTP validation verified successfully."
    assert body["expires_in"] == 1800
    assert SignupVerificationToken.read(body["verification_token"]) == NEW_EMAIL


@pytest.mark.django_db
@pytest.mark.parametrize(
    "body, field",
    [
        ({}, "email"),
        ({"email": NEW_EMAIL}, "code"),
        ({"email": NEW_EMAIL, "code": "12345"}, "code"),
        ({"email": NEW_EMAIL, "code": "12a456"}, "code"),
        ({"email": "not-an-email", "code": "123456"}, "email"),
    ],
)
def test_verify_otp_endpoint_rejects_bad_input(api_client, body, field):
    response = api_client.post(VERIFY_OTP_URL, body, format="json")

    assert response.status_code == 400
    assert field in response.json()


@pytest.mark.django_db
def test_verify_otp_endpoint_code_format_message(api_client):
    response = api_client.post(VERIFY_OTP_URL, {"email": NEW_EMAIL, "code": "12345"}, format="json")

    assert response.json() == {"code": ["Enter the 6-digit code."]}


@pytest.mark.django_db
def test_verify_otp_endpoint_counts_wrong_codes_as_numbers(api_client):
    otp = send_code()
    body = {"email": NEW_EMAIL, "code": wrong_code(otp)}

    first = api_client.post(VERIFY_OTP_URL, body, format="json")
    second = api_client.post(VERIFY_OTP_URL, body, format="json")
    third = api_client.post(VERIFY_OTP_URL, body, format="json")

    assert first.status_code == 400
    assert first.json() == {"error": INCORRECT_CODE_MESSAGE, "attempts_remaining": 2}
    assert second.json() == {"error": INCORRECT_CODE_MESSAGE, "attempts_remaining": 1}
    assert third.status_code == 400
    assert third.json() == {"error": TOO_MANY_ATTEMPTS_MESSAGE}


@pytest.mark.django_db
def test_verify_otp_endpoint_rejects_expired_code(api_client):
    otp = send_code()
    OTP.objects.filter(pk=otp.pk).update(expires_at=timezone.now() - timedelta(seconds=1))

    response = api_client.post(VERIFY_OTP_URL, {"email": NEW_EMAIL, "code": otp.code}, format="json")

    assert response.status_code == 400
    assert response.json() == {"error": CODE_EXPIRED_MESSAGE}


@pytest.mark.django_db
def test_verify_otp_endpoint_without_code_sent(api_client):
    response = api_client.post(VERIFY_OTP_URL, {"email": NEW_EMAIL, "code": "123456"}, format="json")

    assert response.status_code == 400
    assert response.json() == {"error": NO_ACTIVE_CODE_MESSAGE}


@pytest.mark.django_db
def test_resend_otp_endpoint_sends_new_code_after_cooldown(api_client):
    first = send_code()
    age_codes(NEW_EMAIL, 61)

    response = api_client.post(RESEND_OTP_URL, {"email": NEW_EMAIL}, format="json")

    assert response.status_code == 200
    assert response.json() == {"message": "A fresh OTP code has been issued."}
    first.refresh_from_db()
    assert first.is_used is True
    assert OTP.objects.filter(email=NEW_EMAIL, is_used=False).count() == 1


@pytest.mark.django_db
def test_resend_otp_endpoint_rejects_existing_account(api_client):
    create_user("ada@example.com")

    response = api_client.post(RESEND_OTP_URL, {"email": "ada@example.com"}, format="json")

    assert response.status_code == 400
    assert response.json() == {"error": ACCOUNT_EXISTS_MESSAGE}


@pytest.mark.django_db
def test_resend_otp_endpoint_within_cooldown_returns_429(api_client):
    send_code()

    response = api_client.post(RESEND_OTP_URL, {"email": NEW_EMAIL}, format="json")

    assert response.status_code == 429
    assert 1 <= response.json()["retry_after"] <= 60


@pytest.mark.django_db
def test_resend_otp_endpoint_returns_503_when_email_cannot_be_queued(api_client):
    with patch("accounts.services.send_otp_email_task.delay", side_effect=OperationalError("broker down")):
        response = api_client.post(RESEND_OTP_URL, {"email": NEW_EMAIL}, format="json")

    assert response.status_code == 503
    assert OTP.objects.count() == 0


@pytest.mark.django_db
def test_new_code_retires_codes_sent_to_other_letter_cases():
    OTPService.send_verification_code("Victim@Example.com")
    age_codes("Victim@Example.com", 61)

    OTPService.send_verification_code("victim@example.com")

    active = OTP.objects.filter(email__iexact="victim@example.com", is_used=False)
    assert list(active.values_list("email", flat=True)) == ["victim@example.com"]


@pytest.mark.django_db
def test_verify_otp_endpoint_rejects_non_ascii_digits(api_client):
    response = api_client.post(VERIFY_OTP_URL, {"email": NEW_EMAIL, "code": "\u0664\u0668\u0662\u0669\u0661\u0663"}, format="json")

    assert response.status_code == 400
    assert response.json() == {"code": ["Enter the 6-digit code."]}


SIGNUP_URL = "/api/v1/auth/signup/"
GOOD_PASSWORD = "Bright-Ledger-2026!"


def signup_data(email="Juan.Cruz@Agency.gov", **overrides):
    data = {
        "verification_token": SignupVerificationToken.issue(email),
        "full_name": "Juan dela Cruz",
        "organization": "Agency Name",
        "department": "internal_audit",
        "password": GOOD_PASSWORD,
        "re_enter_password": GOOD_PASSWORD,
    }
    data.update(overrides)
    return data


@pytest.mark.django_db
def test_signup_creates_verified_lowercase_user_without_role():
    user = AuthService.signup(signup_data())

    assert user.email == "juan.cruz@agency.gov"
    assert user.is_verified is True
    assert user.role == ""
    assert user.department == "internal_audit"
    assert len(user.username) == 32 and int(user.username, 16) >= 0
    assert user.check_password(GOOD_PASSWORD)
    assert mail.outbox == []


@pytest.mark.django_db
def test_signup_allows_emails_sharing_the_part_before_the_at():
    AuthService.signup(signup_data("john@company-a.com"))
    AuthService.signup(signup_data("john@company-b.com"))

    assert User.objects.filter(email__startswith="john@").count() == 2


@pytest.mark.django_db
def test_signup_rejects_tampered_or_expired_token():
    tampered = signup_data()
    tampered["verification_token"] = tampered["verification_token"].replace("Juan.Cruz", "Evil")
    with pytest.raises(ValidationError) as excinfo:
        AuthService.signup(tampered)
    assert excinfo.value.detail == {"error": TOKEN_INVALID_MESSAGE}

    data = signup_data()
    with patch("django.core.signing.time.time", return_value=time.time() + 31 * 60):
        with pytest.raises(ValidationError):
            AuthService.signup(data)

    assert User.objects.count() == 0


@pytest.mark.django_db
def test_signup_rejects_existing_account_and_token_reuse():
    data = signup_data()
    AuthService.signup(data)

    with pytest.raises(ValidationError) as excinfo:
        AuthService.signup(data)
    assert excinfo.value.detail == {"error": ACCOUNT_EXISTS_MESSAGE}

    create_user("ada@example.com")
    with pytest.raises(ValidationError):
        AuthService.signup(signup_data("ADA@example.com"))


@pytest.mark.django_db
def test_signup_rejects_password_similar_to_the_email():
    data = signup_data("juancruzagency@agency.gov", password="juancruzagency", re_enter_password="juancruzagency")

    with pytest.raises(ValidationError) as excinfo:
        AuthService.signup(data)

    assert "password" in excinfo.value.detail
    assert User.objects.count() == 0


@pytest.mark.django_db
def test_signup_endpoint_returns_201_with_email(api_client):
    response = api_client.post(SIGNUP_URL, signup_data(), format="json")

    assert response.status_code == 201
    assert response.json() == {"message": SIGNUP_SUCCESS_MESSAGE, "email": "juan.cruz@agency.gov"}


@pytest.mark.django_db
@pytest.mark.parametrize(
    "field",
    ["verification_token", "full_name", "organization", "department", "password", "re_enter_password"],
)
def test_signup_endpoint_requires_every_field(api_client, field):
    data = signup_data()
    del data[field]

    response = api_client.post(SIGNUP_URL, data, format="json")

    assert response.status_code == 400
    assert field in response.json()


@pytest.mark.django_db
@pytest.mark.parametrize(
    "overrides, field",
    [
        ({"department": "hr"}, "department"),
        ({"department": ""}, "department"),
        ({"full_name": "   "}, "full_name"),
        ({"re_enter_password": "Different-Pass-1"}, "re_enter_password"),
        ({"password": "Ab1!", "re_enter_password": "Ab1!"}, "password"),
        ({"verification_token": "not-a-token"}, "error"),
    ],
)
def test_signup_endpoint_rejects_invalid_input(api_client, overrides, field):
    response = api_client.post(SIGNUP_URL, signup_data(**overrides), format="json")

    assert response.status_code == 400
    assert field in response.json()
    assert User.objects.count() == 0


@pytest.mark.django_db
def test_signup_endpoint_rejects_existing_account(api_client):
    create_user("ada@example.com")

    response = api_client.post(SIGNUP_URL, signup_data("ada@example.com"), format="json")

    assert response.status_code == 400
    assert response.json() == {"error": ACCOUNT_EXISTS_MESSAGE}


@pytest.mark.django_db
def test_full_registration_flow_through_the_api(api_client):
    api_client.post(SEND_OTP_URL, {"email": NEW_EMAIL}, format="json")
    code = OTP.objects.get(email=NEW_EMAIL).code

    verified = api_client.post(VERIFY_OTP_URL, {"email": NEW_EMAIL, "code": code}, format="json")
    token = verified.json()["verification_token"]
    data = signup_data(verification_token=token)
    created = api_client.post(SIGNUP_URL, data, format="json")

    assert created.status_code == 201
    user = User.objects.get(email=NEW_EMAIL)
    assert user.is_verified is True
    assert user.role == ""
