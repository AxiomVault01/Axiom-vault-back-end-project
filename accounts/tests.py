import os
import subprocess
import sys
import time
from datetime import timedelta
from smtplib import SMTPRecipientsRefused
from unittest.mock import patch

import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import cache
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.db.models.query import QuerySet
from django.utils import timezone
from kombu.exceptions import OperationalError
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIClient
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken
from rest_framework_simplejwt.tokens import AccessToken, RefreshToken

from accounts.models import OTP, Organisation
from accounts.services import (
    ACCOUNT_EXISTS_MESSAGE,
    ORGANISATION_TAKEN_MESSAGE,
    WRONG_SIGN_IN_ROLE_MESSAGE,
    CODE_EXPIRED_MESSAGE,
    DELIVERY_FAILED_MESSAGE,
    EMAIL_NOT_VERIFIED_MESSAGE,
    FORGOT_PASSWORD_MESSAGE,
    INVALID_CREDENTIALS_MESSAGE,
    PASSWORD_CHANGED_MESSAGE,
    PASSWORD_RESET_SUCCESS_MESSAGE,
    CURRENT_PASSWORD_INCORRECT_MESSAGE,
    TOO_MANY_ATTEMPTS_TRY_LATER_MESSAGE,
    RESET_CODE_VERIFIED_MESSAGE,
    RESET_TOKEN_INVALID_MESSAGE,
    INCORRECT_CODE_MESSAGE,
    LOGOUT_SUCCESS_MESSAGE,
    LOGOUT_TOKEN_INVALID_MESSAGE,
    LOGOUT_WRONG_ACCOUNT_MESSAGE,
    NO_ACTIVE_CODE_MESSAGE,
    SESSION_EXPIRED_MESSAGE,
    SIGNUP_SUCCESS_MESSAGE,
    TOKEN_INVALID_MESSAGE,
    TOO_MANY_ATTEMPTS_MESSAGE,
    CodeDeliveryUnavailable,
    CodeRequestTooSoon,
    AuthService,
    CodeVerificationFailed,
    InvalidRefreshToken,
    LogoutTokenInvalid,
    LogoutWrongAccount,
    OTPService,
    PasswordResetToken,
    SignupVerificationToken,
    blacklist_all_refresh_tokens,
    notify_password_changed,
)
from accounts.device import client_ip, describe_user_agent
from accounts.tasks import (
    PASSWORD_CHANGED_EMAIL_SUBJECT,
    PASSWORD_RESET_EMAIL_SUBJECT,
    OTPEmailDeliveryError,
    send_otp_email_task,
    send_password_changed_email_task,
)

User = get_user_model()

NEW_EMAIL = "auditor@agency.gov"


def create_user(email="ada@example.com", is_verified=True, organisation_name=None, **extra):
    # One organisation per user by default: an organisation has at most one super admin.
    organisation, _ = Organisation.objects.get_or_create(name=organisation_name or f"Org of {email.lower()}")
    extra.setdefault("account_type", User.SUPER_ADMIN)
    return User.objects.create_user(
        username=email.split("@")[0],
        email=email,
        password="Str0ng-Passw0rd!",
        full_name="Ada Obi",
        organisation=organisation,
        department="operations",
        role="fraud_analyst",
        is_verified=is_verified,
        **extra,
    )


def age_codes(email, seconds):
    OTP.objects.filter(email=email).update(created_at=timezone.now() - timedelta(seconds=seconds))


@pytest.mark.django_db
def test_new_user_is_unverified_and_password_is_hashed():
    user = User.objects.create_user(
        username="ada", email="ada@example.com", password="Str0ng-Passw0rd!",
        full_name="Ada Obi", department="operations", role="fraud_analyst",
    )

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
    AuthService.signup(signup_data("john@company-a.com", organization="Company A"))
    AuthService.signup(signup_data("john@company-b.com", organization="Company B"))

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


# ---- 3a: F-05, legacy accounts, login ----

@pytest.mark.django_db
def test_signup_rejects_password_similar_to_the_full_name():
    data = signup_data(full_name="Juandelacruz Santos", password="JuandelacruzSantos", re_enter_password="JuandelacruzSantos")

    with pytest.raises(ValidationError) as excinfo:
        AuthService.signup(data)

    assert "password" in excinfo.value.detail


@pytest.mark.django_db
def test_unverified_legacy_account_can_get_a_signup_code():
    create_user("Legacy.User@Example.com", is_verified=False)

    OTPService.send_verification_code("legacy.user@example.com")

    assert OTP.objects.filter(email="legacy.user@example.com").count() == 1


@pytest.mark.django_db
def test_signup_replaces_unverified_legacy_account_in_place():
    legacy = create_user("Legacy.User@Example.com", is_verified=False)

    user = AuthService.signup(signup_data("legacy.user@example.com", department="finance"))

    assert user.id == legacy.id
    assert User.objects.count() == 1
    user.refresh_from_db()
    assert user.email == "legacy.user@example.com"
    assert user.is_verified is True
    assert user.role == ""
    assert user.department == "finance"
    assert user.check_password(GOOD_PASSWORD)
    assert not user.check_password("Str0ng-Passw0rd!")


@pytest.mark.django_db
def test_verified_account_still_blocks_send_otp_and_signup():
    create_user("ada@example.com")

    with pytest.raises(ValidationError):
        OTPService.send_verification_code("ada@example.com")
    with pytest.raises(ValidationError):
        AuthService.signup(signup_data("ada@example.com"))


LOGIN_URL = "/api/v1/auth/login/"
LOGOUT_URL = "/api/v1/auth/logout/"


@pytest.mark.django_db
def test_login_returns_tokens_and_profile():
    user = create_user("ada@example.com", organisation_name="Axiom")

    result = AuthService.login(None, "ada@example.com", "Str0ng-Passw0rd!", "super_admin")

    assert str(AccessToken(result["access"])["user_id"]) == str(user.id)
    assert str(RefreshToken(result["refresh"])["user_id"]) == str(user.id)
    assert result["user"] == {
        "id": str(user.id),
        "email": "ada@example.com",
        "full_name": "Ada Obi",
        "organization": "Axiom",
        "department": "operations",
        "role": "fraud_analyst",
        "account_type": "super_admin",
        "must_change_password": False,
    }
    user.refresh_from_db()
    assert user.last_login is not None


@pytest.mark.django_db
def test_login_accepts_email_in_any_letter_case():
    create_user("Mixed.Case@Example.com")

    stored = User.objects.get().email

    result = AuthService.login(None, "mixed.case@EXAMPLE.com", "Str0ng-Passw0rd!", "super_admin")

    assert result["user"]["email"] == stored


@pytest.mark.django_db
@pytest.mark.parametrize("email, password", [("ada@example.com", "wrong-password"), ("nobody@example.com", "Str0ng-Passw0rd!")])
def test_login_wrong_password_and_unknown_email_look_the_same(api_client, email, password):
    create_user("ada@example.com")

    response = api_client.post(LOGIN_URL, {"email": email, "password": password, "role": "super_admin"}, format="json")

    assert response.status_code == 401
    assert response.json() == {"error": INVALID_CREDENTIALS_MESSAGE}


@pytest.mark.django_db
def test_login_refuses_deactivated_account(api_client):
    create_user("ada@example.com", is_active=False)

    response = api_client.post(LOGIN_URL, {"email": "ada@example.com", "password": "Str0ng-Passw0rd!", "role": "super_admin"}, format="json")

    assert response.status_code == 401
    assert response.json() == {"error": INVALID_CREDENTIALS_MESSAGE}


@pytest.mark.django_db
def test_login_unverified_account_gets_403_only_with_the_right_password(api_client):
    create_user("legacy@example.com", is_verified=False)

    right = api_client.post(LOGIN_URL, {"email": "legacy@example.com", "password": "Str0ng-Passw0rd!", "role": "super_admin"}, format="json")
    wrong = api_client.post(LOGIN_URL, {"email": "legacy@example.com", "password": "nope-nope-nope", "role": "super_admin"}, format="json")

    assert right.status_code == 403
    assert right.json() == {"error": EMAIL_NOT_VERIFIED_MESSAGE}
    assert wrong.status_code == 401


@pytest.mark.django_db
def test_login_endpoint_returns_200_body(api_client):
    create_user("ada@example.com")

    response = api_client.post(LOGIN_URL, {"email": "ada@example.com", "password": "Str0ng-Passw0rd!", "role": "super_admin"}, format="json")

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"access", "refresh", "user"}
    assert body["user"]["email"] == "ada@example.com"


@pytest.mark.django_db
@pytest.mark.parametrize(
    "body, field",
    [({"password": "x"}, "email"), ({"email": "ada@example.com"}, "password"), ({"email": "nope", "password": "x"}, "email")],
)
def test_login_endpoint_rejects_bad_input(api_client, body, field):
    response = api_client.post(LOGIN_URL, body, format="json")

    assert response.status_code == 400
    assert field in response.json()


@pytest.mark.django_db
def test_public_endpoints_ignore_a_stale_authorization_header(api_client):
    create_user("ada@example.com")
    api_client.credentials(HTTP_AUTHORIZATION="Bearer expired-or-garbage")

    login = api_client.post(LOGIN_URL, {"email": "ada@example.com", "password": "Str0ng-Passw0rd!", "role": "super_admin"}, format="json")
    signup = api_client.post(SIGNUP_URL, signup_data("new.person@example.com"), format="json")
    send = api_client.post(SEND_OTP_URL, {"email": "another@example.com"}, format="json")

    assert login.status_code == 200
    assert signup.status_code == 201
    assert send.status_code == 200


@pytest.mark.django_db
def test_access_token_authenticates_a_protected_request(api_client):
    create_user("ada@example.com")
    tokens = api_client.post(LOGIN_URL, {"email": "ada@example.com", "password": "Str0ng-Passw0rd!", "role": "super_admin"}, format="json").json()

    anonymous = api_client.post(LOGOUT_URL, {"refresh": tokens["refresh"]}, format="json")
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")
    authenticated = api_client.post(LOGOUT_URL, {"refresh": tokens["refresh"]}, format="json")

    assert anonymous.status_code == 401
    assert authenticated.status_code == 200


# --- 3b: refresh token ---

REFRESH_URL = "/api/v1/auth/token/refresh/"


def expired_refresh_for(user):
    token = RefreshToken.for_user(user)
    token.set_exp(lifetime=-timedelta(seconds=1))
    return str(token)


def tampered(token):
    # Keep the header and payload but swap in another token's signature, so the signature no longer matches.
    header, payload, _ = token.split(".")
    other_signature = str(RefreshToken.for_user(create_user("other@example.com"))).split(".")[2]
    return f"{header}.{payload}.{other_signature}"


@pytest.mark.django_db
def test_refresh_returns_access_token_for_the_same_user():
    user = create_user()
    refresh = str(RefreshToken.for_user(user))

    result = AuthService.refresh_access_token(refresh)

    assert set(result) == {"access"}
    access = AccessToken(result["access"])
    assert access["user_id"] == str(user.id)
    assert access["token_type"] == "access"


@pytest.mark.django_db
@pytest.mark.parametrize("make_token", [
    expired_refresh_for,
    lambda user: "not-a-jwt",
    lambda user: tampered(str(RefreshToken.for_user(user))),
    lambda user: str(RefreshToken.for_user(user).access_token),
], ids=["expired", "garbage", "bad-signature", "access-token"])
def test_refresh_rejects_bad_tokens(make_token):
    user = create_user()

    with pytest.raises(InvalidRefreshToken) as error:
        AuthService.refresh_access_token(make_token(user))

    assert error.value.detail == {"error": SESSION_EXPIRED_MESSAGE}


@pytest.mark.django_db
def test_refresh_rejects_deactivated_user():
    user = create_user()
    refresh = str(RefreshToken.for_user(user))
    user.is_active = False
    user.save()

    with pytest.raises(InvalidRefreshToken):
        AuthService.refresh_access_token(refresh)


@pytest.mark.django_db
def test_refresh_rejects_deleted_user():
    user = create_user()
    refresh = str(RefreshToken.for_user(user))
    user.delete()

    with pytest.raises(InvalidRefreshToken):
        AuthService.refresh_access_token(refresh)


@pytest.mark.django_db
def test_refresh_endpoint_returns_access_token_that_authenticates(api_client):
    create_user("ada@example.com")
    refresh = api_client.post(LOGIN_URL, {"email": "ada@example.com", "password": "Str0ng-Passw0rd!", "role": "super_admin"}, format="json").json()["refresh"]

    response = api_client.post(REFRESH_URL, {"refresh": refresh}, format="json")

    assert response.status_code == 200
    assert set(response.json()) == {"access"}
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {response.json()['access']}")
    assert api_client.post(LOGOUT_URL, {"refresh": refresh}, format="json").status_code == 200


@pytest.mark.django_db
@pytest.mark.parametrize("body, message", [
    ({}, "This field is required."),
    ({"refresh": ""}, "This field may not be blank."),
])
def test_refresh_endpoint_rejects_missing_or_empty_field(api_client, body, message):
    response = api_client.post(REFRESH_URL, body, format="json")

    assert response.status_code == 400
    assert response.json() == {"refresh": [message]}


@pytest.mark.django_db
@pytest.mark.parametrize("make_token", [
    expired_refresh_for,
    lambda user: "not-a-jwt",
    lambda user: str(RefreshToken.for_user(user).access_token),
], ids=["expired", "garbage", "access-token"])
def test_refresh_endpoint_returns_401_for_unusable_tokens(api_client, make_token):
    user = create_user()

    response = api_client.post(REFRESH_URL, {"refresh": make_token(user)}, format="json")

    assert response.status_code == 401
    assert response.json() == {"error": SESSION_EXPIRED_MESSAGE}


@pytest.mark.django_db
def test_refresh_endpoint_ignores_a_stale_authorization_header(api_client):
    user = create_user()
    api_client.credentials(HTTP_AUTHORIZATION="Bearer expired-or-garbage")

    response = api_client.post(REFRESH_URL, {"refresh": str(RefreshToken.for_user(user))}, format="json")

    assert response.status_code == 200


# --- 3c: logout blacklists the refresh token ---


def login_tokens(api_client, email="ada@example.com"):
    return api_client.post(LOGIN_URL, {"email": email, "password": "Str0ng-Passw0rd!", "role": "super_admin"}, format="json").json()


@pytest.mark.django_db
def test_login_records_the_refresh_token_as_outstanding(api_client):
    user = create_user("ada@example.com")

    refresh = login_tokens(api_client)["refresh"]

    assert OutstandingToken.objects.filter(user=user, jti=RefreshToken(refresh)["jti"]).count() == 1


@pytest.mark.django_db
def test_refresh_endpoint_refuses_a_blacklisted_token(api_client):
    user = create_user()
    token = RefreshToken.for_user(user)
    token.blacklist()

    response = api_client.post(REFRESH_URL, {"refresh": str(token)}, format="json")

    assert response.status_code == 401
    assert response.json() == {"error": SESSION_EXPIRED_MESSAGE}


@pytest.mark.django_db
def test_logout_blacklists_the_callers_refresh_token():
    user = create_user()
    token = RefreshToken.for_user(user)

    AuthService.logout(user, str(token))

    assert BlacklistedToken.objects.filter(token__jti=token["jti"]).exists()


@pytest.mark.django_db
def test_logout_twice_with_the_same_token_is_refused():
    user = create_user()
    refresh = str(RefreshToken.for_user(user))
    AuthService.logout(user, refresh)

    with pytest.raises(LogoutTokenInvalid) as error:
        AuthService.logout(user, refresh)

    assert error.value.detail == {"error": LOGOUT_TOKEN_INVALID_MESSAGE}


@pytest.mark.django_db
@pytest.mark.parametrize("make_token", [
    expired_refresh_for,
    lambda user: "not-a-jwt",
    lambda user: str(RefreshToken.for_user(user).access_token),
], ids=["expired", "garbage", "access-token"])
def test_logout_refuses_unusable_tokens(make_token):
    user = create_user()

    with pytest.raises(LogoutTokenInvalid):
        AuthService.logout(user, make_token(user))

    assert not BlacklistedToken.objects.exists()


@pytest.mark.django_db
def test_logout_refuses_another_users_refresh_token():
    user = create_user()
    other = RefreshToken.for_user(create_user("other@example.com"))

    with pytest.raises(LogoutWrongAccount) as error:
        AuthService.logout(user, str(other))

    assert error.value.detail == {"error": LOGOUT_WRONG_ACCOUNT_MESSAGE}
    assert not BlacklistedToken.objects.filter(token__jti=other["jti"]).exists()


@pytest.mark.django_db
def test_logout_endpoint_blocks_the_refresh_token(api_client):
    create_user("ada@example.com")
    tokens = login_tokens(api_client)
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")

    response = api_client.post(LOGOUT_URL, {"refresh": tokens["refresh"]}, format="json")
    again = api_client.post(LOGOUT_URL, {"refresh": tokens["refresh"]}, format="json")
    api_client.credentials()
    refreshed = api_client.post(REFRESH_URL, {"refresh": tokens["refresh"]}, format="json")

    assert response.status_code == 200
    assert response.json() == {"message": LOGOUT_SUCCESS_MESSAGE}
    assert again.status_code == 400
    assert again.json() == {"error": LOGOUT_TOKEN_INVALID_MESSAGE}
    assert refreshed.status_code == 401


@pytest.mark.django_db
@pytest.mark.parametrize("body, expected", [
    ({}, {"refresh": ["This field is required."]}),
    ({"refresh": ""}, {"refresh": ["This field may not be blank."]}),
    ({"refresh": "not-a-jwt"}, {"error": LOGOUT_TOKEN_INVALID_MESSAGE}),
])
def test_logout_endpoint_rejects_bad_body(api_client, body, expected):
    create_user("ada@example.com")
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {login_tokens(api_client)['access']}")

    response = api_client.post(LOGOUT_URL, body, format="json")

    assert response.status_code == 400
    assert response.json() == expected


@pytest.mark.django_db
@pytest.mark.parametrize("header", [None, "Bearer not-a-jwt"], ids=["no-header", "garbage-access"])
def test_logout_endpoint_requires_a_valid_access_token(api_client, header):
    create_user("ada@example.com")
    refresh = login_tokens(api_client)["refresh"]
    if header:
        api_client.credentials(HTTP_AUTHORIZATION=header)

    response = api_client.post(LOGOUT_URL, {"refresh": refresh}, format="json")

    assert response.status_code == 401
    assert "detail" in response.json()
    assert not BlacklistedToken.objects.exists()


@pytest.mark.django_db
def test_logout_endpoint_refuses_another_accounts_refresh_token(api_client):
    create_user("ada@example.com")
    create_user("other@example.com")
    other_refresh = login_tokens(api_client, "other@example.com")["refresh"]
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {login_tokens(api_client)['access']}")

    response = api_client.post(LOGOUT_URL, {"refresh": other_refresh}, format="json")

    assert response.status_code == 403
    assert response.json() == {"error": LOGOUT_WRONG_ACCOUNT_MESSAGE}
    assert not BlacklistedToken.objects.exists()


# --- 4: forgot and reset password ---

FORGOT_URL = "/api/v1/auth/forgot-password/"
VERIFY_RESET_URL = "/api/v1/auth/verify-reset-code/"
RESET_URL = "/api/v1/auth/reset-password/"
NEW_PASSWORD = "Quiet-Harbor-2026!"


def reset_codes(email="ada@example.com"):
    return OTP.objects.filter(email__iexact=email, purpose="password_reset")


def reset_code_for(email="ada@example.com"):
    AuthService.forgot_password(email)
    return reset_codes(email).latest("created_at")


def reset_token_for(user):
    otp = reset_code_for(user.email)
    return OTPService.verify_reset_code(user.email, otp.code)


def test_reset_email_has_its_own_subject_and_lifetime():
    send_otp_email_task.run("ada@example.com", "123456", "password_reset")
    send_otp_email_task.run("ada@example.com", "654321")

    reset, signup = mail.outbox
    assert reset.subject == PASSWORD_RESET_EMAIL_SUBJECT
    assert "123456" in reset.body and "10 minutes" in reset.body
    assert signup.subject == "Your AxiomVault Verification Code"
    assert signup.body == "Your OTP is 654321"


@pytest.mark.django_db
def test_forgot_password_emails_a_10_minute_code_to_the_stored_email():
    create_user("ada@example.com")

    AuthService.forgot_password("ADA@Example.com")

    otp = reset_codes().get()
    assert otp.email == "ada@example.com"
    assert otp.is_used is False
    lifetime = (otp.expires_at - otp.created_at).total_seconds()
    assert 9 * 60 < lifetime <= 10 * 60
    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == ["ada@example.com"]
    assert mail.outbox[0].subject == PASSWORD_RESET_EMAIL_SUBJECT


@pytest.mark.django_db
@pytest.mark.parametrize("setup", [
    lambda: None,
    lambda: create_user("ada@example.com", is_verified=False),
    lambda: create_user("ada@example.com", is_active=False),
], ids=["unknown", "unverified", "deactivated"])
def test_forgot_password_sends_nothing_to_ineligible_accounts(setup):
    setup()

    AuthService.forgot_password("ada@example.com")

    assert not reset_codes().exists()
    assert mail.outbox == []


@pytest.mark.django_db
def test_forgot_password_cooldown_is_silent_and_keeps_the_first_code():
    create_user("ada@example.com")
    first = reset_code_for()

    AuthService.forgot_password("ada@example.com")

    assert reset_codes().count() == 1
    first.refresh_from_db()
    assert first.is_used is False
    assert len(mail.outbox) == 1


@pytest.mark.django_db
def test_forgot_password_after_cooldown_cancels_the_old_code_but_not_signup_codes():
    create_user("ada@example.com")
    signup = OTP.objects.create(
        email="ada@example.com", code="111111", purpose="verification",
        expires_at=timezone.now() + timedelta(minutes=2),
    )
    first = reset_code_for()
    reset_codes().update(created_at=timezone.now() - timedelta(seconds=61))

    AuthService.forgot_password("ada@example.com")

    first.refresh_from_db()
    signup.refresh_from_db()
    assert first.is_used is True
    assert reset_codes().filter(is_used=False).count() == 1
    assert signup.is_used is False


@pytest.mark.django_db
def test_forgot_password_broker_failure_leaves_no_new_code():
    create_user("ada@example.com")

    with patch("accounts.services.send_otp_email_task.delay", side_effect=OperationalError("broker down")):
        with pytest.raises(CodeDeliveryUnavailable):
            AuthService.forgot_password("ada@example.com")

    assert not reset_codes().exists()


@pytest.mark.django_db
def test_forgot_password_endpoint_gives_the_same_answer_for_every_email(api_client):
    create_user("ada@example.com")
    api_client.credentials(HTTP_AUTHORIZATION="Bearer expired-or-garbage")

    known = api_client.post(FORGOT_URL, {"email": "ada@example.com"}, format="json")
    unknown = api_client.post(FORGOT_URL, {"email": "nobody@example.com"}, format="json")

    assert known.status_code == unknown.status_code == 200
    assert known.json() == unknown.json() == {"message": FORGOT_PASSWORD_MESSAGE}
    assert reset_codes().count() == 1


@pytest.mark.django_db
@pytest.mark.parametrize("body", [{}, {"email": "not-an-email"}])
def test_forgot_password_endpoint_rejects_bad_email(api_client, body):
    response = api_client.post(FORGOT_URL, body, format="json")

    assert response.status_code == 400
    assert "email" in response.json()


@pytest.mark.django_db
def test_forgot_password_endpoint_returns_503_when_email_cannot_be_queued(api_client):
    create_user("ada@example.com")

    with patch("accounts.services.send_otp_email_task.delay", side_effect=OperationalError("broker down")):
        response = api_client.post(FORGOT_URL, {"email": "ada@example.com"}, format="json")

    assert response.status_code == 503
    assert response.json() == {"error": DELIVERY_FAILED_MESSAGE}


@pytest.mark.django_db
def test_correct_reset_code_returns_a_token_for_the_user_and_uses_the_code():
    user = create_user("ada@example.com")
    otp = reset_code_for()

    token = OTPService.verify_reset_code("ADA@example.com", otp.code)

    assert PasswordResetToken.read(token) == user
    otp.refresh_from_db()
    assert otp.is_used is True


@pytest.mark.django_db
def test_wrong_reset_codes_count_down_then_cancel_the_code():
    create_user("ada@example.com")
    otp = reset_code_for()

    for remaining in (2, 1):
        with pytest.raises(CodeVerificationFailed) as error:
            OTPService.verify_reset_code("ada@example.com", wrong_code(otp))
        assert error.value.detail == {"error": INCORRECT_CODE_MESSAGE, "attempts_remaining": remaining}
    with pytest.raises(CodeVerificationFailed) as error:
        OTPService.verify_reset_code("ada@example.com", wrong_code(otp))

    assert error.value.detail == {"error": TOO_MANY_ATTEMPTS_MESSAGE}
    with pytest.raises(CodeVerificationFailed) as error:
        OTPService.verify_reset_code("ada@example.com", otp.code)
    assert error.value.detail == {"error": NO_ACTIVE_CODE_MESSAGE}


@pytest.mark.django_db
def test_expired_reset_code_is_refused():
    create_user("ada@example.com")
    otp = reset_code_for()
    reset_codes().update(expires_at=timezone.now() - timedelta(seconds=1))

    with pytest.raises(CodeVerificationFailed) as error:
        OTPService.verify_reset_code("ada@example.com", otp.code)

    assert error.value.detail == {"error": CODE_EXPIRED_MESSAGE}


@pytest.mark.django_db
def test_signup_and_reset_codes_cannot_be_swapped():
    create_user("ada@example.com")
    reset_otp = reset_code_for()
    signup_otp = OTP.objects.create(
        email="ada@example.com", code=wrong_code(reset_otp), purpose="verification",
        expires_at=timezone.now() + timedelta(minutes=2),
    )

    with pytest.raises(CodeVerificationFailed):
        OTPService.verify_signup_code("ada@example.com", reset_otp.code)
    with pytest.raises(CodeVerificationFailed):
        OTPService.verify_reset_code("ada@example.com", signup_otp.code)


@pytest.mark.django_db
def test_reset_code_for_an_account_deactivated_after_sending_is_refused():
    user = create_user("ada@example.com")
    otp = reset_code_for()
    user.is_active = False
    user.save()

    with pytest.raises(CodeVerificationFailed) as error:
        OTPService.verify_reset_code("ada@example.com", otp.code)

    assert error.value.detail == {"error": NO_ACTIVE_CODE_MESSAGE}


@pytest.mark.django_db
def test_verify_reset_code_endpoint_returns_token(api_client):
    create_user("ada@example.com")
    otp = reset_code_for()

    response = api_client.post(VERIFY_RESET_URL, {"email": "ada@example.com", "code": otp.code}, format="json")

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"message", "reset_token", "expires_in"}
    assert body["message"] == RESET_CODE_VERIFIED_MESSAGE
    assert body["expires_in"] == 15 * 60


@pytest.mark.django_db
@pytest.mark.parametrize("body, field", [
    ({"code": "123456"}, "email"),
    ({"email": "ada@example.com"}, "code"),
    ({"email": "ada@example.com", "code": "12ab56"}, "code"),
])
def test_verify_reset_code_endpoint_rejects_bad_input(api_client, body, field):
    response = api_client.post(VERIFY_RESET_URL, body, format="json")

    assert response.status_code == 400
    assert field in response.json()


@pytest.mark.django_db
def test_verify_reset_code_endpoint_counts_wrong_codes(api_client):
    create_user("ada@example.com")
    otp = reset_code_for()

    response = api_client.post(VERIFY_RESET_URL, {"email": "ada@example.com", "code": wrong_code(otp)}, format="json")

    assert response.status_code == 400
    assert response.json() == {"error": INCORRECT_CODE_MESSAGE, "attempts_remaining": 2}


@pytest.mark.django_db
def test_reset_password_changes_password_and_ends_every_session(api_client):
    user = create_user("ada@example.com")
    old_refresh = login_tokens(api_client)["refresh"]
    token = reset_token_for(user)

    response = api_client.post(
        RESET_URL,
        {"reset_token": token, "new_password": NEW_PASSWORD, "re_enter_password": NEW_PASSWORD},
        format="json",
    )

    assert response.status_code == 200
    assert response.json() == {"message": PASSWORD_RESET_SUCCESS_MESSAGE}
    user.refresh_from_db()
    assert user.check_password(NEW_PASSWORD)
    assert not user.check_password("Str0ng-Passw0rd!")
    assert api_client.post(REFRESH_URL, {"refresh": old_refresh}, format="json").status_code == 401
    new_login = api_client.post(LOGIN_URL, {"email": "ada@example.com", "password": NEW_PASSWORD, "role": "super_admin"}, format="json")
    assert new_login.status_code == 200


@pytest.mark.django_db
def test_reset_token_works_only_once(api_client):
    user = create_user("ada@example.com")
    token = reset_token_for(user)
    body = {"reset_token": token, "new_password": NEW_PASSWORD, "re_enter_password": NEW_PASSWORD}

    first = api_client.post(RESET_URL, body, format="json")
    second = api_client.post(RESET_URL, {**body, "new_password": "Other-Harbor-2026!",
                                         "re_enter_password": "Other-Harbor-2026!"}, format="json")

    assert first.status_code == 200
    assert second.status_code == 400
    assert second.json() == {"error": RESET_TOKEN_INVALID_MESSAGE}
    user.refresh_from_db()
    assert user.check_password(NEW_PASSWORD)


@pytest.mark.django_db
def test_expired_or_garbage_reset_token_is_refused(api_client, settings):
    user = create_user("ada@example.com")
    token = reset_token_for(user)
    settings.PASSWORD_RESET_TIMEOUT = -1

    for bad in (token, "garbage", "bm90LWEtdXVpZA:abc", ""):
        response = api_client.post(
            RESET_URL,
            {"reset_token": bad, "new_password": NEW_PASSWORD, "re_enter_password": NEW_PASSWORD},
            format="json",
        )
        assert response.status_code == 400
    assert not User.objects.get(pk=user.pk).check_password(NEW_PASSWORD)


@pytest.mark.django_db
@pytest.mark.parametrize("new_password", ["short", "password123", "Ada Obi 1", "ada@example.com1"])
def test_reset_password_refuses_weak_passwords(api_client, new_password):
    user = create_user("ada@example.com")
    token = reset_token_for(user)

    response = api_client.post(
        RESET_URL, {"reset_token": token, "new_password": new_password, "re_enter_password": new_password},
        format="json",
    )

    assert response.status_code == 400
    assert "new_password" in response.json()
    user.refresh_from_db()
    assert user.check_password("Str0ng-Passw0rd!")


@pytest.mark.django_db
def test_reset_password_refuses_mismatched_passwords(api_client):
    user = create_user("ada@example.com")

    response = api_client.post(
        RESET_URL,
        {"reset_token": reset_token_for(user), "new_password": NEW_PASSWORD, "re_enter_password": "Different-2026!"},
        format="json",
    )

    assert response.status_code == 400
    assert response.json() == {"re_enter_password": ["Passwords do not match."]}


@pytest.mark.django_db
def test_signup_code_is_queued_with_the_original_two_arguments():
    # Keeps signup emails working on a Celery worker that still runs the pre-reset code.
    create_user("ada@example.com")
    with patch("accounts.services.send_otp_email_task.delay") as delay:
        OTPService.send_verification_code(NEW_EMAIL)
        AuthService.forgot_password("ada@example.com")

    signup_call, reset_call = delay.call_args_list
    assert signup_call.args == (NEW_EMAIL, signup_call.args[1]) and signup_call.kwargs == {}
    assert reset_call.kwargs == {"purpose": "password_reset"}


# --- 5: change password ---


@pytest.mark.django_db
def test_reset_logs_out_every_device(api_client):
    user = create_user("ada@example.com")
    first_device = login_tokens(api_client)["refresh"]
    second_device = login_tokens(api_client)["refresh"]

    AuthService.reset_password(reset_token_for(user), NEW_PASSWORD)

    for refresh in (first_device, second_device):
        assert api_client.post(REFRESH_URL, {"refresh": refresh}, format="json").status_code == 401


@pytest.mark.django_db
def test_blacklisting_everything_twice_is_harmless():
    user = create_user("ada@example.com")
    RefreshToken.for_user(user)
    already = RefreshToken.for_user(user)
    already.blacklist()

    blacklist_all_refresh_tokens(user)
    blacklist_all_refresh_tokens(user)

    assert BlacklistedToken.objects.filter(token__user=user).count() == 2


CHROME_ON_WINDOWS = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/129.0.0.0 Safari/537.36"
)


@pytest.mark.parametrize("user_agent, expected", [
    (CHROME_ON_WINDOWS, "Chrome on Windows"),
    ("", "Unknown device"),
    ("garbage", "Unknown device"),
    ("curl/8.4.0", "curl"),
])
def test_describe_user_agent(user_agent, expected):
    assert describe_user_agent(user_agent) == expected


def test_describe_user_agent_strips_control_characters_and_limits_length():
    text = describe_user_agent("Evil\r\nInjected: header " + "A" * 500)

    assert "\n" not in text and "\r" not in text
    assert len(text) <= 100


@pytest.mark.parametrize("meta, expected", [
    ({"HTTP_X_FORWARDED_FOR": "1.2.3.4, 5.6.7.8", "REMOTE_ADDR": "10.0.0.1"}, "5.6.7.8"),
    ({"REMOTE_ADDR": "10.0.0.1"}, "10.0.0.1"),
    ({"HTTP_X_FORWARDED_FOR": "not-an-ip", "REMOTE_ADDR": "10.0.0.1"}, "Unknown"),
    ({}, "Unknown"),
    ({"REMOTE_ADDR": "2001:db8::1"}, "2001:db8::1"),
])
def test_client_ip(meta, expected):
    assert client_ip(meta) == expected


def test_password_changed_email_text():
    send_password_changed_email_task.run(
        "ada@example.com", "2 Oct 2026, 10:15 UTC", "changed from your account settings",
        "Chrome on Windows", "5.6.7.8",
    )

    email = mail.outbox[0]
    assert email.subject == PASSWORD_CHANGED_EMAIL_SUBJECT
    for text in ("2 Oct 2026, 10:15 UTC", "changed from your account settings", "Chrome on Windows",
                 "5.6.7.8", "If this wasn't you"):
        assert text in email.body


@pytest.mark.django_db
def test_notification_queue_failure_does_not_raise_or_log_the_address(caplog, django_capture_on_commit_callbacks):
    user = create_user("ada@example.com")

    with patch("accounts.services.send_password_changed_email_task.delay", side_effect=OperationalError("down")):
        with django_capture_on_commit_callbacks(execute=True):
            notify_password_changed(user, "changed from your account settings", {"device": "X", "ip": "1.2.3.4"})

    assert "could not be queued" in caplog.text
    assert "ada@example.com" not in caplog.text
    assert "1.2.3.4" not in caplog.text


CHANGE_URL = "/api/v1/auth/change-password/"
DEVICE = {"device": "Chrome on Windows", "ip": "5.6.7.8"}


@pytest.mark.django_db
def test_change_password_ends_sessions_returns_new_tokens_and_notifies(api_client, django_capture_on_commit_callbacks):
    user = create_user("ada@example.com")
    old_refresh = [login_tokens(api_client)["refresh"], login_tokens(api_client)["refresh"]]

    with django_capture_on_commit_callbacks(execute=True):
        result = AuthService.change_password(user, "Str0ng-Passw0rd!", NEW_PASSWORD, DEVICE)

    assert set(result) == {"message", "access", "refresh"}
    assert result["message"] == PASSWORD_CHANGED_MESSAGE
    assert AccessToken(result["access"])["user_id"] == str(user.id)
    user.refresh_from_db()
    assert user.check_password(NEW_PASSWORD)
    for refresh in old_refresh:
        assert api_client.post(REFRESH_URL, {"refresh": refresh}, format="json").status_code == 401
    assert api_client.post(REFRESH_URL, {"refresh": result["refresh"]}, format="json").status_code == 200
    notice = [m for m in mail.outbox if m.subject == PASSWORD_CHANGED_EMAIL_SUBJECT]
    assert len(notice) == 1
    assert notice[0].to == ["ada@example.com"]
    assert "changed from your account settings" in notice[0].body
    assert "Chrome on Windows" in notice[0].body and "5.6.7.8" in notice[0].body


@pytest.mark.django_db
def test_change_password_with_wrong_current_password_changes_nothing(django_capture_on_commit_callbacks):
    user = create_user("ada@example.com")
    RefreshToken.for_user(user)

    with django_capture_on_commit_callbacks(execute=True):
        with pytest.raises(ValidationError) as error:
            AuthService.change_password(user, "wrong-password", NEW_PASSWORD, DEVICE)

    assert error.value.detail == {"current_password": [CURRENT_PASSWORD_INCORRECT_MESSAGE]}
    user.refresh_from_db()
    assert user.check_password("Str0ng-Passw0rd!")
    assert not BlacklistedToken.objects.exists()
    assert mail.outbox == []


@pytest.mark.django_db
@pytest.mark.parametrize("new_password", ["short", "password123", "Ada Obi 1"])
def test_change_password_refuses_weak_new_passwords(new_password):
    user = create_user("ada@example.com")

    with pytest.raises(ValidationError) as error:
        AuthService.change_password(user, "Str0ng-Passw0rd!", new_password, DEVICE)

    assert "new_password" in error.value.detail
    user.refresh_from_db()
    assert user.check_password("Str0ng-Passw0rd!")


@pytest.mark.django_db
def test_reset_password_sends_the_notification(django_capture_on_commit_callbacks):
    user = create_user("ada@example.com")
    token = reset_token_for(user)
    mail.outbox.clear()

    with django_capture_on_commit_callbacks(execute=True):
        AuthService.reset_password(token, NEW_PASSWORD, DEVICE)

    assert len(mail.outbox) == 1
    assert mail.outbox[0].subject == PASSWORD_CHANGED_EMAIL_SUBJECT
    assert "reset with an emailed code" in mail.outbox[0].body


@pytest.fixture(autouse=True)
def clear_throttle_counts():
    # Throttle counts live in the cache; start every test from zero.
    cache.clear()
    yield
    cache.clear()


def change_body(current="Str0ng-Passw0rd!", new=NEW_PASSWORD, again=None):
    return {"current_password": current, "new_password": new, "re_enter_password": again or new}


def logged_in(api_client, email="ada@example.com"):
    tokens = login_tokens(api_client, email)
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")
    return tokens


@pytest.mark.django_db
def test_change_password_endpoint_returns_new_tokens(api_client):
    create_user("ada@example.com")
    old = logged_in(api_client)

    response = api_client.post(CHANGE_URL, change_body(), format="json", HTTP_USER_AGENT=CHROME_ON_WINDOWS)

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"message", "access", "refresh"}
    api_client.credentials()
    assert api_client.post(REFRESH_URL, {"refresh": old["refresh"]}, format="json").status_code == 401


@pytest.mark.django_db
@pytest.mark.parametrize("body, expected", [
    (change_body(current="wrong"), {"current_password": [CURRENT_PASSWORD_INCORRECT_MESSAGE]}),
    (change_body(again="Different-2026!"), {"re_enter_password": ["Passwords do not match."]}),
    ({"new_password": NEW_PASSWORD, "re_enter_password": NEW_PASSWORD}, {"current_password": ["This field is required."]}),
])
def test_change_password_endpoint_rejects_bad_input(api_client, body, expected):
    create_user("ada@example.com")
    logged_in(api_client)

    response = api_client.post(CHANGE_URL, body, format="json")

    assert response.status_code == 400
    assert response.json() == expected


@pytest.mark.django_db
def test_change_password_endpoint_rejects_weak_password(api_client):
    create_user("ada@example.com")
    logged_in(api_client)

    response = api_client.post(CHANGE_URL, change_body(new="12345678"), format="json")

    assert response.status_code == 400
    assert "new_password" in response.json()


@pytest.mark.django_db
@pytest.mark.parametrize("header", [None, "Bearer not-a-jwt"], ids=["no-header", "garbage-access"])
def test_change_password_endpoint_requires_login(api_client, header):
    create_user("ada@example.com")
    if header:
        api_client.credentials(HTTP_AUTHORIZATION=header)

    response = api_client.post(CHANGE_URL, change_body(), format="json")

    assert response.status_code == 401


@pytest.mark.django_db
def test_change_password_allows_five_attempts_then_429(api_client):
    create_user("ada@example.com")
    logged_in(api_client)

    statuses = [api_client.post(CHANGE_URL, change_body(current="wrong"), format="json").status_code for _ in range(5)]
    blocked = api_client.post(CHANGE_URL, change_body(), format="json")

    assert statuses == [400] * 5
    assert blocked.status_code == 429
    assert blocked.json()["error"] == TOO_MANY_ATTEMPTS_TRY_LATER_MESSAGE
    assert 0 < blocked.json()["retry_after"] <= 15 * 60
    assert int(blocked["Retry-After"]) > 0


@pytest.mark.django_db
def test_change_password_limit_is_per_user(api_client):
    create_user("ada@example.com")
    create_user("other@example.com")
    logged_in(api_client)
    for _ in range(5):
        api_client.post(CHANGE_URL, change_body(current="wrong"), format="json")

    logged_in(api_client, "other@example.com")
    response = api_client.post(CHANGE_URL, change_body(current="wrong"), format="json")

    assert response.status_code == 400


@pytest.mark.django_db
def test_other_auth_endpoints_are_not_throttled(api_client):
    create_user("ada@example.com")

    statuses = {
        api_client.post(LOGIN_URL, {"email": "ada@example.com", "password": "wrong", "role": "super_admin"}, format="json").status_code
        for _ in range(8)
    }

    assert statuses == {401}


# --- 5: deploy readiness ---


@pytest.mark.parametrize("value, expected", [
    (None, ["http://local"]),
    ("", ["http://local"]),
    ("https://app.example.com", ["https://app.example.com"]),
    (" https://a.example.com , ,https://b.example.com ", ["https://a.example.com", "https://b.example.com"]),
])
def test_env_list_reads_comma_separated_origins(monkeypatch, value, expected):
    from config.settings import env_list

    if value is None:
        monkeypatch.delenv("CORS_TEST_ORIGINS", raising=False)
    else:
        monkeypatch.setenv("CORS_TEST_ORIGINS", value)

    assert env_list("CORS_TEST_ORIGINS", ["http://local"]) == expected


def run_settings_import(env_overrides):
    env = {**os.environ, **env_overrides}
    return subprocess.run(
        [sys.executable, "-c", "import config.settings"],
        cwd=settings.BASE_DIR, env=env, capture_output=True, text=True,
    )


def test_production_refuses_to_start_without_a_secret_key():
    # An empty value is present in the environment, so load_dotenv() does not fill it from .env.
    result = run_settings_import({"ENV": "prod", "SECRET_KEY": ""})

    assert result.returncode != 0
    assert "SECRET_KEY must be set when ENV=prod" in result.stderr


def test_production_starts_with_a_secret_key():
    result = run_settings_import({"ENV": "prod", "SECRET_KEY": "a-real-production-key-0123456789abcdef"})

    assert result.returncode == 0, result.stderr


def test_whitenoise_serves_static_files():
    assert "whitenoise.middleware.WhiteNoiseMiddleware" in settings.MIDDLEWARE
    assert settings.MIDDLEWARE.index("whitenoise.middleware.WhiteNoiseMiddleware") == (
        settings.MIDDLEWARE.index("django.middleware.security.SecurityMiddleware") + 1
    )


# --- fix: SMTP timeout ---


def test_smtp_connection_gives_up_after_10_seconds():
    from django.core.mail.backends.smtp import EmailBackend

    assert settings.EMAIL_TIMEOUT == 10
    assert EmailBackend().timeout == 10


def test_smtp_timeout_is_reported_without_the_address(caplog):
    address = "victim@example.com"

    with patch("accounts.tasks.send_mail", side_effect=TimeoutError("timed out")):
        with pytest.raises(OTPEmailDeliveryError):
            send_otp_email_task.run(address, "123456")

    assert "TimeoutError" in caplog.text
    assert address not in caplog.text
    assert "123456" not in caplog.text


# ---- 13: Organisations table ----

ORGANISATION_TAKEN = {"organization": [ORGANISATION_TAKEN_MESSAGE]}


@pytest.mark.django_db
def test_signup_creates_one_organisation_for_the_new_user():
    user = AuthService.signup(signup_data(organization="  Ikeja Cooperative  "))

    organisation = Organisation.objects.get()
    assert organisation.name == "Ikeja Cooperative"
    assert user.organisation == organisation


@pytest.mark.django_db
@pytest.mark.parametrize("taken_name", ["Ikeja Cooperative", "IKEJA cooperative", "  ikeja cooperative "])
def test_signup_endpoint_refuses_a_taken_organisation_name(api_client, taken_name):
    Organisation.objects.create(name="Ikeja Cooperative")
    data = signup_data(organization=taken_name)

    refused = api_client.post(SIGNUP_URL, data, format="json")

    assert refused.status_code == 400
    assert refused.json() == ORGANISATION_TAKEN
    assert User.objects.count() == 0
    assert Organisation.objects.count() == 1

    # The same verification token still works with a free name.
    data["organization"] = "Lekki Cooperative"
    created = api_client.post(SIGNUP_URL, data, format="json")
    assert created.status_code == 201
    assert User.objects.get().organisation.name == "Lekki Cooperative"


@pytest.mark.django_db
def test_legacy_signup_renames_its_own_organisation_and_may_keep_the_name():
    legacy = create_user("legacy.user@example.com", is_verified=False, organisation_name="Old Name")
    organisation_id = legacy.organisation_id

    user = AuthService.signup(signup_data("legacy.user@example.com", organization="old name"))

    assert user.organisation_id == organisation_id
    assert Organisation.objects.get().name == "old name"


@pytest.mark.django_db
def test_legacy_signup_cannot_take_another_organisations_name():
    create_user("ada@example.com", organisation_name="Ikeja Cooperative")
    legacy = create_user("legacy.user@example.com", is_verified=False, organisation_name="Old Name")

    with pytest.raises(ValidationError) as excinfo:
        AuthService.signup(signup_data("legacy.user@example.com", organization="ikeja cooperative"))

    assert excinfo.value.detail == ORGANISATION_TAKEN
    legacy.refresh_from_db()
    assert legacy.organisation.name == "Old Name"
    assert legacy.is_verified is False


@pytest.mark.django_db
def test_legacy_signup_without_an_organisation_gets_a_new_one():
    legacy = create_user("legacy.user@example.com", is_verified=False)
    User.objects.filter(pk=legacy.pk).update(organisation=None)

    user = AuthService.signup(signup_data("legacy.user@example.com", organization="Fresh Cooperative"))

    assert user.organisation.name == "Fresh Cooperative"


@pytest.mark.django_db
def test_failed_signup_creates_no_organisation():
    with pytest.raises(ValidationError):
        AuthService.signup(signup_data(password="short", re_enter_password="short"))

    duplicate_email = IntegrityError('duplicate key value violates unique constraint "accounts_user_email_key"')
    with patch.object(User.objects, "create_user", side_effect=duplicate_email):
        with pytest.raises(ValidationError) as excinfo:
            AuthService.signup(signup_data())

    assert excinfo.value.detail == {"error": ACCOUNT_EXISTS_MESSAGE}
    assert Organisation.objects.count() == 0


@pytest.mark.django_db
def test_racing_signups_for_one_name_get_the_organisation_error():
    Organisation.objects.create(name="Ikeja Cooperative")

    # The name check misses the other signup's row, so the database constraint has to stop it.
    with patch.object(QuerySet, "exists", return_value=False):
        with pytest.raises(ValidationError) as excinfo:
            AuthService.signup(signup_data(organization="ikeja COOPERATIVE"))

    assert excinfo.value.detail == ORGANISATION_TAKEN
    assert User.objects.count() == 0


@pytest.mark.django_db
def test_organisation_names_are_unique_ignoring_letter_case():
    Organisation.objects.create(name="Ikeja Cooperative")

    with pytest.raises(IntegrityError), transaction.atomic():
        Organisation.objects.create(name="IKEJA COOPERATIVE")


@pytest.mark.django_db
def test_login_shows_an_empty_organisation_for_staff_without_one():
    user = create_user("staff@example.com")
    User.objects.filter(pk=user.pk).update(organisation=None)

    result = AuthService.login(None, "staff@example.com", "Str0ng-Passw0rd!", "super_admin")

    assert result["user"]["organization"] == ""


BEFORE_ORGANISATIONS = [("accounts", "0003_alter_user_role")]
WITH_ORGANISATIONS = [("accounts", "0006_remove_user_organization_and_more")]


def migrate_to(target):
    executor = MigrationExecutor(connection)
    executor.migrate(target)
    return executor.loader.project_state(target).apps


@pytest.mark.django_db(transaction=True)
def test_organisation_migrations_move_the_text_both_ways():
    try:
        old_apps = migrate_to(BEFORE_ORGANISATIONS)
        OldUser = old_apps.get_model("accounts", "User")
        start = timezone.now()
        texts = {"first@example.com": "Ikeja Coop", "second@example.com": " ikeja coop ", "staff@example.com": ""}
        for offset, (email, text) in enumerate(texts.items()):
            OldUser.objects.create(
                username=email, email=email, organization=text, date_joined=start + timedelta(seconds=offset)
            )

        new_apps = migrate_to(WITH_ORGANISATIONS)
        NewUser = new_apps.get_model("accounts", "User")
        moved = {u.email: u.organisation.name if u.organisation else None for u in NewUser.objects.all()}
        assert moved == {
            "first@example.com": "Ikeja Coop",
            "second@example.com": "ikeja coop (2)",
            "staff@example.com": None,
        }
        with pytest.raises(IntegrityError), transaction.atomic():
            new_apps.get_model("accounts", "Organisation").objects.create(name="IKEJA COOP")

        old_apps = migrate_to(BEFORE_ORGANISATIONS)
        restored = dict(old_apps.get_model("accounts", "User").objects.values_list("email", "organization"))
        assert restored == {
            "first@example.com": "Ikeja Coop",
            "second@example.com": "ikeja coop (2)",
            "staff@example.com": "",
        }
    finally:
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())


@pytest.mark.django_db
def test_createsuperuser_works_without_an_organisation():
    from django.core.management import call_command

    call_command("createsuperuser", interactive=False, username="admin", email="admin@example.com", verbosity=0)

    assert User.objects.get(username="admin").organisation is None


# ---- 29: Super admin signup and role-checked login ----


@pytest.mark.django_db
def test_signup_makes_the_new_user_the_super_admin():
    user = AuthService.signup(signup_data())

    assert user.account_type == "super_admin"


@pytest.mark.django_db
def test_legacy_signup_makes_the_account_super_admin():
    legacy = create_user("legacy.user@example.com", is_verified=False, account_type="")

    user = AuthService.signup(signup_data("legacy.user@example.com"))

    assert user.id == legacy.id
    assert user.account_type == "super_admin"


@pytest.mark.django_db
def test_an_organisation_cannot_have_two_super_admins():
    first = create_user("first@example.com")

    with pytest.raises(IntegrityError), transaction.atomic():
        create_user("second@example.com", organisation_name=first.organisation.name)


@pytest.mark.django_db
def test_team_members_may_share_an_organisation_with_its_super_admin():
    admin = create_user("admin.coop@example.com")

    create_user("member.coop@example.com", organisation_name=admin.organisation.name, account_type="team_member")

    assert admin.organisation.users.count() == 2


BEFORE_ACCOUNT_TYPES = [("accounts", "0006_remove_user_organization_and_more")]
WITH_ACCOUNT_TYPES = [("accounts", "0007_user_account_type")]


@pytest.mark.django_db(transaction=True)
def test_account_type_migration_makes_existing_accounts_super_admins():
    try:
        old_apps = migrate_to(BEFORE_ACCOUNT_TYPES)
        OldUser = old_apps.get_model("accounts", "User")
        OldOrganisation = old_apps.get_model("accounts", "Organisation")
        coop = OldOrganisation.objects.create(name="Ikeja Coop")
        OldUser.objects.create(username="owner", email="owner@example.com", organisation=coop)
        OldUser.objects.create(username="staff", email="staff@example.com", is_staff=True)

        new_apps = migrate_to(WITH_ACCOUNT_TYPES)
        types = dict(new_apps.get_model("accounts", "User").objects.values_list("email", "account_type"))
        assert types == {"owner@example.com": "super_admin", "staff@example.com": ""}

        migrate_to(BEFORE_ACCOUNT_TYPES)
    finally:
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())


@pytest.mark.django_db
@pytest.mark.parametrize("flag", ["is_staff", "is_superuser"])
def test_signup_cannot_take_over_a_staff_or_superuser_account(flag):
    staff = create_user("ops@example.com", is_verified=False, account_type="", **{flag: True})
    User.objects.filter(pk=staff.pk).update(organisation=None)

    with pytest.raises(ValidationError) as excinfo:
        AuthService.signup(signup_data("ops@example.com"))

    assert excinfo.value.detail == {"error": ACCOUNT_EXISTS_MESSAGE}
    staff.refresh_from_db()
    assert staff.check_password("Str0ng-Passw0rd!")
    assert staff.is_verified is False
    assert staff.organisation is None
    assert staff.account_type == ""


@pytest.mark.django_db
def test_legacy_signup_leaves_a_shared_organisation_alone():
    legacy = create_user("legacy.user@example.com", is_verified=False, organisation_name="Shared Coop", account_type="")
    create_user("colleague@example.com", organisation_name="Shared Coop", account_type="team_member")

    user = AuthService.signup(signup_data("legacy.user@example.com", organization="Fresh Coop"))

    assert user.organisation.name == "Fresh Coop"
    assert user.organisation_id != legacy.organisation_id
    assert Organisation.objects.get(pk=legacy.organisation_id).name == "Shared Coop"


WRONG_ROLE = {"error": WRONG_SIGN_IN_ROLE_MESSAGE}


@pytest.mark.django_db
def test_login_with_the_matching_role_returns_the_account_type(api_client):
    create_user("ada@example.com")

    response = api_client.post(
        LOGIN_URL, {"email": "ada@example.com", "password": "Str0ng-Passw0rd!", "role": "super_admin"}, format="json"
    )

    assert response.status_code == 200
    assert response.json()["user"]["account_type"] == "super_admin"


@pytest.mark.django_db
def test_team_member_logs_in_only_as_team_member(api_client):
    admin = create_user("admin.coop@example.com")
    create_user("member.coop@example.com", organisation_name=admin.organisation.name, account_type="team_member")
    body = {"email": "member.coop@example.com", "password": "Str0ng-Passw0rd!"}

    as_member = api_client.post(LOGIN_URL, {**body, "role": "team_member"}, format="json")
    as_admin = api_client.post(LOGIN_URL, {**body, "role": "super_admin"}, format="json")

    assert as_member.status_code == 200
    assert as_member.json()["user"]["account_type"] == "team_member"
    assert as_admin.status_code == 403
    assert as_admin.json() == WRONG_ROLE


@pytest.mark.django_db
def test_wrong_role_with_the_right_password_gets_403_and_no_tokens(api_client):
    create_user("ada@example.com")

    response = api_client.post(
        LOGIN_URL, {"email": "ada@example.com", "password": "Str0ng-Passw0rd!", "role": "team_member"}, format="json"
    )

    assert response.status_code == 403
    assert response.json() == WRONG_ROLE
    assert OutstandingToken.objects.count() == 0


@pytest.mark.django_db
@pytest.mark.parametrize("role", ["super_admin", "team_member"])
def test_wrong_password_gets_the_plain_401_whatever_the_role(api_client, role):
    create_user("ada@example.com")

    response = api_client.post(LOGIN_URL, {"email": "ada@example.com", "password": "nope-nope-nope", "role": role}, format="json")

    assert response.status_code == 401
    assert response.json() == {"error": INVALID_CREDENTIALS_MESSAGE}


@pytest.mark.django_db
@pytest.mark.parametrize(
    "role, error",
    [(None, "This field is required."), ("admin", '"admin" is not a valid choice.'), ("", '"" is not a valid choice.')],
)
def test_login_requires_a_valid_role(api_client, role, error):
    create_user("ada@example.com")
    body = {"email": "ada@example.com", "password": "Str0ng-Passw0rd!"}
    if role is not None:
        body["role"] = role

    response = api_client.post(LOGIN_URL, body, format="json")

    assert response.status_code == 400
    assert response.json() == {"role": [error]}


@pytest.mark.django_db
@pytest.mark.parametrize("role", ["super_admin", "team_member"])
def test_staff_account_without_a_type_cannot_log_in_to_the_app(api_client, role):
    staff = create_user("ops@example.com", account_type="", is_staff=True)
    User.objects.filter(pk=staff.pk).update(organisation=None)

    response = api_client.post(LOGIN_URL, {"email": "ops@example.com", "password": "Str0ng-Passw0rd!", "role": role}, format="json")

    assert response.status_code == 403
    assert response.json() == WRONG_ROLE


# ---- 30: Team member management ----

TEAM_URL = "/api/v1/team/members/"
FRAUD_RESULTS_URL = "/api/v1/fraud/results/"
TEMP_PASSWORD = "Temp-Ledger-2026!"
SUPER_ADMIN_ONLY = {"error": "Only the super admin can manage the team."}
PASSWORD_CHANGE_REQUIRED = {
    "error": "Change your temporary password before continuing.",
    "code": "password_change_required",
}


def member_url(member_id, action=""):
    return f"{TEAM_URL}{member_id}/" + (f"{action}/" if action else "")


def bearer(client, user):
    client.credentials(HTTP_AUTHORIZATION="Bearer " + str(AccessToken.for_user(user)))
    return client


def new_member_data(**overrides):
    data = {
        "full_name": "Ada Obi",
        "phone_number": "+234 801-234-5678",
        "role": "Payroll Analyst",
        "email": "Ada.Obi@IkejaCoop.ng",
        "password": TEMP_PASSWORD,
    }
    data.update(overrides)
    return data


@pytest.fixture
def super_admin():
    return create_user("owner@ikejacoop.ng", organisation_name="Ikeja Coop")


def add_member(admin, **overrides):
    response = bearer(APIClient(), admin).post(TEAM_URL, new_member_data(**overrides), format="json")
    assert response.status_code == 201, response.json()
    return User.objects.get(pk=response.json()["id"])


def member_login_body(member, password=TEMP_PASSWORD):
    return {"email": member.email, "password": password, "role": "team_member"}


@pytest.mark.django_db
def test_super_admin_adds_a_team_member(api_client, super_admin):
    response = bearer(api_client, super_admin).post(TEAM_URL, new_member_data(), format="json")

    assert response.status_code == 201
    body = response.json()
    assert set(body) == {
        "id", "full_name", "email", "phone_number", "role", "is_active", "must_change_password", "date_joined"
    }
    assert body["email"] == "ada.obi@ikejacoop.ng"
    assert body["phone_number"] == "+2348012345678"
    assert body["must_change_password"] is True
    member = User.objects.get(pk=body["id"])
    assert member.account_type == "team_member"
    assert member.organisation == super_admin.organisation
    assert member.is_verified is True
    assert member.check_password(TEMP_PASSWORD) and member.password != TEMP_PASSWORD


@pytest.mark.django_db
@pytest.mark.parametrize(
    "overrides, field",
    [
        ({"full_name": ""}, "full_name"),
        ({"phone_number": "12345"}, "phone_number"),
        ({"phone_number": "080-ABC-1234"}, "phone_number"),
        ({"role": ""}, "role"),
        ({"role": "R" * 101}, "role"),
        ({"email": "not-an-email"}, "email"),
        ({"password": "short"}, "password"),
        ({"password": "AdaObiIkeja"}, "password"),
    ],
)
def test_add_member_rejects_invalid_fields(api_client, super_admin, overrides, field):
    response = bearer(api_client, super_admin).post(TEAM_URL, new_member_data(**overrides), format="json")

    assert response.status_code == 400
    assert field in response.json()
    assert not User.objects.filter(account_type="team_member").exists()


@pytest.mark.django_db
def test_add_member_requires_every_field(api_client, super_admin):
    response = bearer(api_client, super_admin).post(TEAM_URL, {}, format="json")

    assert response.status_code == 400
    assert set(response.json()) == {"full_name", "phone_number", "role", "email", "password"}


@pytest.mark.django_db
def test_add_member_refuses_an_email_in_use_in_any_letter_case(api_client, super_admin):
    create_user("taken@example.com")

    response = bearer(api_client, super_admin).post(TEAM_URL, new_member_data(email="TAKEN@example.com"), format="json")

    assert response.status_code == 400
    assert response.json() == {"email": ["An account with this email already exists."]}


@pytest.mark.django_db
def test_team_endpoints_need_a_token(api_client):
    assert api_client.get(TEAM_URL).status_code == 401
    assert api_client.post(TEAM_URL, new_member_data(), format="json").status_code == 401


@pytest.mark.django_db
def test_team_member_cannot_manage_the_team(api_client, super_admin):
    member = add_member(super_admin)
    User.objects.filter(pk=member.pk).update(must_change_password=False)
    member.refresh_from_db()

    as_member = bearer(api_client, member)
    listed = as_member.get(TEAM_URL)
    added = as_member.post(TEAM_URL, new_member_data(email="x@ikejacoop.ng"), format="json")

    assert listed.status_code == added.status_code == 403
    assert added.json() == SUPER_ADMIN_ONLY


@pytest.mark.django_db
def test_list_and_detail_show_only_my_own_team(api_client, super_admin):
    mine = add_member(super_admin)
    other_admin = create_user("boss@lekkicoop.ng", organisation_name="Lekki Coop")
    theirs = add_member(other_admin, email="tunde@lekkicoop.ng")

    client = bearer(api_client, super_admin)
    listed = client.get(TEAM_URL)

    assert listed.status_code == 200
    assert [m["id"] for m in listed.json()] == [str(mine.id)]
    assert client.get(member_url(mine.id)).json()["email"] == mine.email
    assert client.get(member_url(theirs.id)).status_code == 404
    assert client.get(member_url(super_admin.id)).status_code == 404


@pytest.mark.django_db
def test_list_is_newest_first(api_client, super_admin):
    first = add_member(super_admin, email="first@ikejacoop.ng")
    second = add_member(super_admin, email="second@ikejacoop.ng")
    User.objects.filter(pk=first.pk).update(date_joined=timezone.now() - timedelta(days=1))

    listed = bearer(api_client, super_admin).get(TEAM_URL).json()

    assert [m["id"] for m in listed] == [str(second.id), str(first.id)]


@pytest.mark.django_db
def test_edit_changes_only_the_fields_sent(api_client, super_admin):
    member = add_member(super_admin)

    response = bearer(api_client, super_admin).patch(
        member_url(member.id),
        {"role": " Senior Payroll Analyst ", "account_type": "super_admin", "is_active": False, "organisation": None},
        format="json",
    )

    assert response.status_code == 200
    member.refresh_from_db()
    assert member.role == "Senior Payroll Analyst"
    assert member.full_name == "Ada Obi"
    assert member.account_type == "team_member"
    assert member.is_active is True
    assert member.organisation == super_admin.organisation


@pytest.mark.django_db
def test_edit_rejects_a_taken_email_and_bad_values(api_client, super_admin):
    member = add_member(super_admin)
    create_user("taken@example.com")
    client = bearer(api_client, super_admin)

    assert client.patch(member_url(member.id), {"email": "Taken@Example.com"}, format="json").status_code == 400
    assert client.patch(member_url(member.id), {"phone_number": "12"}, format="json").status_code == 400
    assert client.patch(member_url(member.id), {"email": "ADA.OBI@ikejacoop.ng"}, format="json").status_code == 200
    assert client.put(member_url(member.id), new_member_data(), format="json").status_code == 405


@pytest.mark.django_db
def test_deactivate_ends_sessions_and_reactivate_restores_login(api_client, super_admin):
    member = add_member(super_admin)
    User.objects.filter(pk=member.pk).update(must_change_password=False)
    refresh = APIClient().post(LOGIN_URL, member_login_body(member), format="json").json()["refresh"]
    admin_client = bearer(api_client, super_admin)

    first = admin_client.post(member_url(member.id, "deactivate"))
    again = admin_client.post(member_url(member.id, "deactivate"))

    assert first.status_code == again.status_code == 200
    assert first.json()["is_active"] is False
    assert APIClient().post(REFRESH_URL, {"refresh": refresh}, format="json").status_code == 401
    assert APIClient().post(LOGIN_URL, member_login_body(member), format="json").status_code == 401

    restored = admin_client.post(member_url(member.id, "reactivate"))
    assert restored.status_code == 200 and restored.json()["is_active"] is True
    assert APIClient().post(LOGIN_URL, member_login_body(member), format="json").status_code == 200


@pytest.mark.django_db
def test_edit_and_deactivate_404_for_another_organisation(api_client, super_admin):
    other_admin = create_user("boss@lekkicoop.ng", organisation_name="Lekki Coop")
    theirs = add_member(other_admin, email="tunde@lekkicoop.ng")
    client = bearer(api_client, super_admin)

    assert client.patch(member_url(theirs.id), {"role": "x"}, format="json").status_code == 404
    assert client.post(member_url(theirs.id, "deactivate")).status_code == 404
    assert client.post(member_url(theirs.id, "reactivate")).status_code == 404
    theirs.refresh_from_db()
    assert theirs.is_active is True and theirs.role == "Payroll Analyst"


@pytest.mark.django_db
def test_new_member_must_change_the_temporary_password_first(super_admin):
    member = add_member(super_admin)
    client = APIClient()

    login = client.post(LOGIN_URL, member_login_body(member), format="json")
    assert login.status_code == 200
    assert login.json()["user"]["must_change_password"] is True
    client.credentials(HTTP_AUTHORIZATION="Bearer " + login.json()["access"])

    blocked = client.get(FRAUD_RESULTS_URL)
    assert blocked.status_code == 403
    assert blocked.json() == PASSWORD_CHANGE_REQUIRED

    changed = client.post(
        CHANGE_URL,
        {"current_password": TEMP_PASSWORD, "new_password": "Own-Ledger-2026!", "re_enter_password": "Own-Ledger-2026!"},
        format="json",
    )
    assert changed.status_code == 200, changed.json()
    member.refresh_from_db()
    assert member.must_change_password is False

    # The block is gone: the team list now refuses only because a member is not the super admin.
    client.credentials(HTTP_AUTHORIZATION="Bearer " + changed.json()["access"])
    assert client.get(TEAM_URL).json() == SUPER_ADMIN_ONLY


@pytest.mark.django_db
def test_member_with_a_temporary_password_can_still_log_out(super_admin):
    member = add_member(super_admin)
    client = APIClient()
    tokens = client.post(LOGIN_URL, member_login_body(member), format="json").json()
    client.credentials(HTTP_AUTHORIZATION="Bearer " + tokens["access"])

    assert client.post(LOGOUT_URL, {"refresh": tokens["refresh"]}, format="json").status_code == 200


@pytest.mark.django_db
def test_password_reset_also_clears_the_temporary_password_flag(super_admin):
    member = add_member(super_admin)

    AuthService.reset_password(PasswordResetToken.issue(member), "Own-Ledger-2026!")

    member.refresh_from_db()
    assert member.must_change_password is False


# ---- 6: Role-based authorization ----

SOME_ID = "3f2a7c9e-1b4d-4e8a-9c2f-6d5e4b3a2c1d"
PROTECTED_ROUTES = [
    ("get", "/api/v1/fraud/"),
    ("get", "/api/v1/fraud/results/"),
    ("get", f"/api/v1/fraud/results/{SOME_ID}/"),
    ("get", "/api/v1/fraud/results/flagged/"),
    ("get", "/api/v1/fraud/alerts/"),
    ("post", "/api/v1/fraud/alerts/"),
    ("get", f"/api/v1/fraud/alerts/{SOME_ID}/"),
    ("put", f"/api/v1/fraud/alerts/{SOME_ID}/"),
    ("patch", f"/api/v1/fraud/alerts/{SOME_ID}/"),
    ("delete", f"/api/v1/fraud/alerts/{SOME_ID}/"),
    ("patch", f"/api/v1/fraud/alerts/{SOME_ID}/change_status/"),
    ("get", f"/api/v1/fraud/alerts/{SOME_ID}/audit_logs/"),
    ("post", "/api/v1/ingestion/upload-payroll/"),
]


@pytest.mark.django_db
@pytest.mark.parametrize("method, url", PROTECTED_ROUTES)
def test_fraud_and_ingestion_routes_need_a_token(api_client, method, url):
    response = getattr(api_client, method)(url, {}, format="json")

    assert response.status_code == 401
    assert response.json() == {"detail": "Authentication credentials were not provided."}


@pytest.mark.django_db
@pytest.mark.parametrize("method, url", PROTECTED_ROUTES[:2])
def test_a_bad_token_is_refused(api_client, method, url):
    api_client.credentials(HTTP_AUTHORIZATION="Bearer not-a-real-token")

    assert getattr(api_client, method)(url).status_code == 401


@pytest.mark.django_db
@pytest.mark.parametrize("url", ["/api/docs/", "/api/schema/", "/api/redoc/"])
def test_api_docs_stay_public(api_client, url):
    assert api_client.get(url).status_code == 200


def permission_outcome(view_class, user, action="list"):
    """Runs a real view's permission checks for a user without touching its (not yet migrated) tables."""
    from rest_framework.exceptions import NotAuthenticated, PermissionDenied
    from rest_framework.test import APIRequestFactory, force_authenticate

    request = APIRequestFactory().get("/")
    force_authenticate(request, user=user)
    view = view_class()
    view.action_map = {"get": action}
    view.action = action
    view.request = view.initialize_request(request)
    view.format_kwarg = None
    try:
        view.check_permissions(view.request)
    except NotAuthenticated:
        return 401
    except PermissionDenied as exc:
        return exc.detail
    return "allowed"


def protected_views():
    from fraud.viewsets import AlertViewSet, FraudResultViewSet
    from ingestion.viewsets import IngestionViewSet

    return [(FraudResultViewSet, "list"), (AlertViewSet, "list"), (IngestionViewSet, "upload_payroll")]


@pytest.mark.django_db
def test_super_admin_and_team_member_may_both_use_fraud_and_ingestion():
    admin = create_user("owner@ikejacoop.ng", organisation_name="Ikeja Coop")
    member = create_user("ada@ikejacoop.ng", organisation_name="Ikeja Coop", account_type="team_member")

    for view_class, action in protected_views():
        assert permission_outcome(view_class, admin, action) == "allowed"
        assert permission_outcome(view_class, member, action) == "allowed"


@pytest.mark.django_db
def test_member_on_a_temporary_password_is_blocked_from_fraud_and_ingestion():
    create_user("owner@ikejacoop.ng", organisation_name="Ikeja Coop")
    member = create_user(
        "ada@ikejacoop.ng", organisation_name="Ikeja Coop", account_type="team_member", must_change_password=True
    )

    for view_class, action in protected_views():
        assert permission_outcome(view_class, member, action) == PASSWORD_CHANGE_REQUIRED


@pytest.mark.django_db
def test_only_the_super_admin_manages_the_team():
    from accounts.team_viewsets import TeamMemberViewSet

    admin = create_user("owner@ikejacoop.ng", organisation_name="Ikeja Coop")
    member = create_user("ada@ikejacoop.ng", organisation_name="Ikeja Coop", account_type="team_member")
    staff = create_user("ops@example.com", account_type="", is_staff=True)

    assert permission_outcome(TeamMemberViewSet, admin) == "allowed"
    assert permission_outcome(TeamMemberViewSet, member) == SUPER_ADMIN_ONLY
    assert permission_outcome(TeamMemberViewSet, staff) == SUPER_ADMIN_ONLY


@pytest.mark.django_db
def test_public_auth_endpoints_ignore_the_login_requirement(api_client):
    create_user("ada@example.com")

    login = api_client.post(LOGIN_URL, {"email": "ada@example.com", "password": "Str0ng-Passw0rd!", "role": "super_admin"}, format="json")
    refreshed = api_client.post(REFRESH_URL, {"refresh": login.json()["refresh"]}, format="json")
    forgot = api_client.post(FORGOT_URL, {"email": "nobody@example.com"}, format="json")

    assert login.status_code == 200
    assert refreshed.status_code == 200
    assert forgot.status_code == 200


# ---- 14: Audit log foundation ----

from django.core.exceptions import PermissionDenied as DjangoPermissionDenied

from accounts.models import AuditLog

CLIENT_IP = "203.0.113.7"


def ip_client():
    return APIClient(REMOTE_ADDR=CLIENT_IP)


def only_row(action):
    rows = list(AuditLog.objects.filter(action=action))
    assert len(rows) == 1, [r.action for r in AuditLog.objects.all()]
    return rows[0]


@pytest.mark.django_db
def test_record_writes_one_row_with_snapshots():
    user = create_user("ada@ikejacoop.ng", organisation_name="Ikeja Coop")

    row = AuditLog.record(AuditLog.LOGIN, actor=user, target=user, details={"role": "super_admin"})

    assert row.actor == user and row.actor_email == "ada@ikejacoop.ng"
    assert row.organisation == user.organisation and row.organisation_name == "Ikeja Coop"
    assert row.target_type == "user" and row.target_id == str(user.pk)
    assert row.details == {"role": "super_admin"}
    assert row.ip_address is None


@pytest.mark.django_db
def test_audit_rows_cannot_be_changed_or_deleted():
    row = AuditLog.record(AuditLog.LOGIN, actor=create_user())

    row.details = {"edited": True}
    with pytest.raises(DjangoPermissionDenied):
        row.save()
    with pytest.raises(DjangoPermissionDenied):
        row.delete()
    with pytest.raises(DjangoPermissionDenied):
        AuditLog.objects.filter(pk=row.pk).update(action=AuditLog.LOGOUT)
    with pytest.raises(DjangoPermissionDenied):
        AuditLog.objects.all().delete()
    assert AuditLog.objects.get(pk=row.pk).details == {}


@pytest.mark.django_db
def test_rows_survive_deleting_the_user_and_organisation():
    user = create_user("ada@ikejacoop.ng", organisation_name="Ikeja Coop")
    organisation = user.organisation
    row = AuditLog.record(AuditLog.LOGIN, actor=user)

    user.delete()
    organisation.delete()

    row = AuditLog.objects.get(pk=row.pk)
    assert row.actor is None and row.organisation is None
    assert row.actor_email == "ada@ikejacoop.ng" and row.organisation_name == "Ikeja Coop"


@pytest.mark.django_db
def test_signup_and_login_are_recorded_with_the_ip():
    client = ip_client()
    client.post(SIGNUP_URL, signup_data(organization="Ikeja Coop"), format="json")
    client.post(LOGIN_URL, {"email": "juan.cruz@agency.gov", "password": GOOD_PASSWORD, "role": "super_admin"}, format="json")

    user = User.objects.get(email="juan.cruz@agency.gov")
    signup = only_row(AuditLog.SIGNUP)
    login = only_row(AuditLog.LOGIN)
    assert signup.actor == user and signup.target_id == str(user.pk) and signup.ip_address == CLIENT_IP
    assert login.actor == user and login.organisation_name == "Ikeja Coop"
    assert login.details == {"role": "super_admin"} and login.ip_address == CLIENT_IP


@pytest.mark.django_db
def test_logout_and_password_changes_are_recorded():
    user = create_user("ada@example.com")
    client = ip_client()
    tokens = client.post(LOGIN_URL, {"email": "ada@example.com", "password": "Str0ng-Passw0rd!", "role": "super_admin"}, format="json").json()
    client.credentials(HTTP_AUTHORIZATION="Bearer " + tokens["access"])

    client.post(CHANGE_URL, {"current_password": "Str0ng-Passw0rd!", "new_password": "Own-Ledger-2026!", "re_enter_password": "Own-Ledger-2026!"}, format="json")
    fresh = client.post(LOGIN_URL, {"email": "ada@example.com", "password": "Own-Ledger-2026!", "role": "super_admin"}, format="json").json()
    client.credentials(HTTP_AUTHORIZATION="Bearer " + fresh["access"])
    client.post(LOGOUT_URL, {"refresh": fresh["refresh"]}, format="json")
    user.refresh_from_db()  # The reset token depends on the current password hash.
    client.post(RESET_URL, {"reset_token": PasswordResetToken.issue(user), "new_password": "Reset-Ledger-2026!", "re_enter_password": "Reset-Ledger-2026!"}, format="json")

    assert only_row(AuditLog.PASSWORD_CHANGED).actor == user
    assert only_row(AuditLog.LOGOUT).actor == user
    assert only_row(AuditLog.PASSWORD_RESET).actor == user


@pytest.mark.django_db
def test_team_actions_are_recorded_against_the_member(super_admin):
    member = add_member(super_admin)
    client = bearer(ip_client(), super_admin)

    client.patch(member_url(member.id), {"role": "Lead Analyst", "phone_number": "08012345678"}, format="json")
    client.post(member_url(member.id, "deactivate"))
    client.post(member_url(member.id, "reactivate"))

    added = only_row(AuditLog.TEAM_MEMBER_ADDED)
    assert added.actor == super_admin and added.target_id == str(member.pk)
    assert added.organisation == super_admin.organisation and added.details == {"email": member.email}
    updated = only_row(AuditLog.TEAM_MEMBER_UPDATED)
    assert updated.details == {"fields": ["phone_number", "role"]} and updated.ip_address == CLIENT_IP
    assert only_row(AuditLog.TEAM_MEMBER_DEACTIVATED).target_id == str(member.pk)
    assert only_row(AuditLog.TEAM_MEMBER_REACTIVATED).target_id == str(member.pk)


@pytest.mark.django_db
def test_failed_actions_write_nothing(super_admin):
    client = APIClient()
    client.post(LOGIN_URL, {"email": "owner@ikejacoop.ng", "password": "wrong-wrong-1", "role": "super_admin"}, format="json")
    client.post(LOGIN_URL, {"email": "owner@ikejacoop.ng", "password": "Str0ng-Passw0rd!", "role": "team_member"}, format="json")
    client.post(SIGNUP_URL, signup_data(password="short", re_enter_password="short"), format="json")
    bearer(client, super_admin).post(TEAM_URL, new_member_data(phone_number="12"), format="json")
    client.post(member_url(SOME_ID, "deactivate"))

    assert AuditLog.objects.count() == 0
