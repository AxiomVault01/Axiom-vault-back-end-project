import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from accounts.models import AuditLog, Organisation

User = get_user_model()
LOGIN = "/api/v1/admin/auth/login/"
PASSWORD = "Str0ng-Passw0rd!"


@pytest.fixture
def owner(db):
    # Like `createsuperuser`: superuser and staff, never verified through signup.
    return User.objects.create_superuser(username="owner", email="owner@axiomvault.ng", password=PASSWORD)


@pytest.fixture
def staff(db):
    return User.objects.create_user(username="support", email="support@axiomvault.ng", password=PASSWORD, full_name="Kemi Support", is_staff=True)


def sign_in(email, password=PASSWORD, client=None):
    return (client or APIClient()).post(LOGIN, {"email": email, "password": password}, format="json")


def test_owner_signs_in_as_owner(owner):
    response = sign_in("OWNER@axiomvault.ng")

    assert response.status_code == 200
    body = response.json()
    assert body["access"] and body["refresh"]
    assert body["user"] == {
        "id": str(owner.id), "email": "owner@axiomvault.ng", "full_name": "", "admin_role": "owner",
        "must_change_password": False, "profile_picture": None,
    }
    owner.refresh_from_db()
    assert owner.last_login is not None
    row = AuditLog.objects.get(action=AuditLog.LOGIN)
    assert row.actor == owner and row.details == {"panel": "admin", "admin_role": "owner"}


def test_staff_sign_in_as_staff(staff):
    body = sign_in("support@axiomvault.ng").json()

    assert body["user"]["admin_role"] == "staff" and body["user"]["full_name"] == "Kemi Support"


def test_staff_on_a_temporary_password_are_told(staff):
    User.objects.filter(pk=staff.pk).update(must_change_password=True)

    assert sign_in("support@axiomvault.ng").json()["user"]["must_change_password"] is True


@pytest.mark.parametrize("email, password", [("support@axiomvault.ng", "wrong-password"), ("nobody@axiomvault.ng", PASSWORD)])
def test_wrong_credentials_are_401(staff, email, password):
    response = sign_in(email, password)

    assert response.status_code == 401 and response.json() == {"error": "Invalid email or password."}
    assert not AuditLog.objects.exists()


def test_deactivated_staff_are_401(staff):
    User.objects.filter(pk=staff.pk).update(is_active=False)

    assert sign_in("support@axiomvault.ng").status_code == 401


def test_cooperative_accounts_are_refused_with_the_right_password(db):
    org = Organisation.objects.create(name="Ikeja Coop")
    User.objects.create_user(username="ngozi", email="ngozi@ikejacoop.ng", password=PASSWORD, organisation=org, account_type="super_admin", is_verified=True)

    response = sign_in("ngozi@ikejacoop.ng")

    assert response.status_code == 403
    assert response.json() == {"error": "This account can't sign in to the Admin Control Panel.", "code": "not_admin"}
    assert not AuditLog.objects.exists()


@pytest.mark.parametrize("body", [{}, {"email": "support@axiomvault.ng"}, {"email": "not-an-email", "password": PASSWORD}])
def test_bad_bodies_are_400(staff, body):
    assert APIClient().post(LOGIN, body, format="json").status_code == 400


def test_a_stale_token_does_not_block_sign_in(staff):
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION="Bearer expired.or.garbage")

    assert sign_in("support@axiomvault.ng", client=client).status_code == 200


def test_admin_tokens_work_with_refresh_and_logout(staff):
    tokens = sign_in("support@axiomvault.ng").json()

    refreshed = APIClient().post("/api/v1/auth/token/refresh/", {"refresh": tokens["refresh"]}, format="json")
    assert refreshed.status_code == 200 and refreshed.json()["access"]

    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION="Bearer " + tokens["access"])
    assert client.post("/api/v1/auth/logout/", {"refresh": tokens["refresh"]}, format="json").status_code == 200
    assert APIClient().post("/api/v1/auth/token/refresh/", {"refresh": tokens["refresh"]}, format="json").status_code == 401


def test_staff_cannot_use_the_cooperative_login(staff):
    response = APIClient().post("/api/v1/auth/login/", {"email": "support@axiomvault.ng", "password": PASSWORD, "role": "super_admin"}, format="json")

    assert response.status_code in (401, 403)


def test_staff_on_a_temporary_password_can_change_it(staff):
    User.objects.filter(pk=staff.pk).update(must_change_password=True)
    tokens = sign_in("support@axiomvault.ng").json()
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION="Bearer " + tokens["access"])

    response = client.post(
        "/api/v1/auth/change-password/",
        {"current_password": PASSWORD, "new_password": "N3w-Support-Pass!", "re_enter_password": "N3w-Support-Pass!"},
        format="json",
    )

    assert response.status_code == 200, response.json()
    assert sign_in("support@axiomvault.ng", "N3w-Support-Pass!").json()["user"]["must_change_password"] is False
