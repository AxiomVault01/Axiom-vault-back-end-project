from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.core import mail
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken, RefreshToken

from accounts.models import OTP, AuditLog, Organisation

User = get_user_model()
USERS = "/api/v1/admin/users/"
STAFF = "/api/v1/admin/staff/"
PASSWORD = "Str0ng-Passw0rd!"
TEMP = "Temp-Pass-2026!"


@pytest.fixture
def owner(db):
    return User.objects.create_superuser(username="owner", email="owner@axiomvault.ng", password=PASSWORD)


@pytest.fixture
def staff(db):
    return User.objects.create_user(username="support", email="support@axiomvault.ng", password=PASSWORD, full_name="Kemi Support", is_staff=True, is_verified=True)


@pytest.fixture
def org(db):
    return Organisation.objects.create(name="Ikeja Coop")


@pytest.fixture
def boss(org):
    return User.objects.create_user(
        username="ngozi", email="ngozi@ikejacoop.ng", password=PASSWORD, full_name="Ngozi Okafor", organisation=org,
        account_type="super_admin", is_verified=True,
    )


@pytest.fixture
def member(org):
    return User.objects.create_user(
        username="ada", email="ada@ikejacoop.ng", password=PASSWORD, full_name="Ada Obi", organisation=org,
        account_type="team_member", role="Payroll Analyst", is_verified=True,
    )


def client_for(user):
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION="Bearer " + str(AccessToken.for_user(user)))
    return client


# ---- step 1: Cooperative users ----


def test_list_users_across_organisations(staff, owner, boss, member, org):
    other = Organisation.objects.create(name="Lekki Coop")
    User.objects.create_user(username="x", email="x@lekki.ng", password=PASSWORD, organisation=other, account_type="super_admin", is_verified=True)
    client = client_for(staff)

    body = client.get(USERS).json()
    assert body["count"] == 3  # Staff and the owner are not users here.
    row = next(r for r in body["results"] if r["email"] == "ada@ikejacoop.ng")
    assert row["organisation"] == {"id": str(org.id), "name": "Ikeja Coop", "status": "active"}
    assert row["account_type"] == "team_member" and row["role"] == "Payroll Analyst" and row["is_active"] is True

    assert client.get(USERS, {"organisation": str(org.id)}).json()["count"] == 2
    assert [r["email"] for r in client.get(USERS, {"account_type": "team_member"}).json()["results"]] == ["ada@ikejacoop.ng"]
    assert [r["email"] for r in client.get(USERS, {"search": "NGOZI"}).json()["results"]] == ["ngozi@ikejacoop.ng"]
    User.objects.filter(pk=member.pk).update(is_active=False)
    assert [r["email"] for r in client.get(USERS, {"is_active": "false"}).json()["results"]] == ["ada@ikejacoop.ng"]
    assert client.get(USERS, {"account_type": "staff"}).status_code == 400


def test_create_a_super_admin_for_a_new_organisation(staff):
    org = Organisation.objects.create(name="Lekki Coop")

    response = client_for(staff).post(USERS, {
        "organisation_id": str(org.id), "account_type": "super_admin", "full_name": " Bola Ade ",
        "email": "Bola@Lekki.ng", "password": TEMP, "phone_number": "+234 803-000-0001",
    }, format="json")

    assert response.status_code == 201, response.json()
    created = User.objects.get(email="bola@lekki.ng")
    assert (created.full_name, created.account_type, created.organisation, created.phone_number) == ("Bola Ade", "super_admin", org, "+2348030000001")
    assert created.must_change_password and created.is_verified and created.check_password(TEMP)
    row = AuditLog.objects.get(action=AuditLog.USER_CREATED)
    assert row.actor == staff and row.organisation == org and row.details["kind"] == "user"
    login = APIClient().post("/api/v1/auth/login/", {"email": "bola@lekki.ng", "password": TEMP, "role": "super_admin"}, format="json")
    assert login.status_code == 200 and login.json()["user"]["must_change_password"] is True


@pytest.mark.parametrize(
    "change, field",
    [
        ({"email": "ngozi@ikejacoop.ng"}, "email"),
        ({"account_type": "super_admin"}, "account_type"),
        ({"organisation_id": "00000000-0000-0000-0000-000000000000"}, "organisation_id"),
        ({"password": "password"}, "password"),
        ({"phone_number": "12ab"}, "phone_number"),
        ({"account_type": "staff"}, "account_type"),
    ],
)
def test_create_refusals(staff, boss, org, change, field):
    body = {"organisation_id": str(org.id), "account_type": "team_member", "full_name": "New", "email": "new@ikejacoop.ng", "password": TEMP, **change}

    response = client_for(staff).post(USERS, body, format="json")

    assert response.status_code == 400 and field in response.json()
    assert not User.objects.filter(email="new@ikejacoop.ng").exists()


def test_edit_a_user(staff, member, boss):
    client = client_for(staff)

    assert client.patch(f"{USERS}{member.id}/", {"email": "ngozi@ikejacoop.ng"}, format="json").json() == {"email": ["An account with this email already exists."]}
    response = client.patch(f"{USERS}{member.id}/", {"role": "Senior Analyst", "phone_number": "", "account_type": "super_admin"}, format="json")

    assert response.status_code == 200
    member.refresh_from_db()
    assert member.role == "Senior Analyst" and member.account_type == "team_member"
    assert AuditLog.objects.get(action=AuditLog.USER_UPDATED).details["fields"] == ["role"]


def test_deactivate_signs_out_and_reactivate(staff, member):
    refresh = str(RefreshToken.for_user(member))
    client = client_for(staff)

    body = client.post(f"{USERS}{member.id}/deactivate/").json()
    assert body["is_active"] is False
    assert APIClient().post("/api/v1/auth/token/refresh/", {"refresh": refresh}, format="json").status_code == 401
    assert client.post(f"{USERS}{member.id}/deactivate/").status_code == 200  # Twice is fine.
    assert AuditLog.objects.filter(action=AuditLog.USER_DEACTIVATED).count() == 1

    assert client.post(f"{USERS}{member.id}/reactivate/").json()["is_active"] is True
    assert AuditLog.objects.filter(action=AuditLog.USER_REACTIVATED).count() == 1


def test_staff_accounts_are_not_reachable_as_users(staff, owner):
    client = client_for(staff)

    assert client.get(f"{USERS}{owner.id}/").status_code == 404
    assert client.post(f"{USERS}{staff.id}/deactivate/").status_code == 404
    assert client.get(f"{USERS}not-an-id/").status_code == 404


def test_users_screen_is_staff_only(boss):
    assert APIClient().get(USERS).status_code == 401
    assert client_for(boss).get(USERS).json()["code"] == "not_admin"


# ---- step 2: password reset ----


def test_send_a_password_reset(staff, member, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        response = client_for(staff).post(f"{USERS}{member.id}/reset-password/")

    assert response.status_code == 200 and response.json() == {"message": "A password reset code has been emailed to ada@ikejacoop.ng."}
    code = OTP.objects.get(email="ada@ikejacoop.ng", purpose="password_reset", is_used=False).code
    assert len(mail.outbox) == 1 and code in mail.outbox[0].body
    assert AuditLog.objects.get(action=AuditLog.PASSWORD_RESET_SENT).details["email"] == "ada@ikejacoop.ng"
    verified = APIClient().post("/api/v1/auth/verify-reset-code/", {"email": "ada@ikejacoop.ng", "code": code}, format="json")
    assert verified.status_code == 200


def test_reset_cooldown_and_deactivated(staff, member):
    client = client_for(staff)
    client.post(f"{USERS}{member.id}/reset-password/")

    again = client.post(f"{USERS}{member.id}/reset-password/")
    assert again.status_code == 429 and 0 < again.json()["retry_after"] <= 60

    OTP.objects.update(created_at=timezone.now() - timedelta(minutes=2))
    User.objects.filter(pk=member.pk).update(is_active=False)
    assert client.post(f"{USERS}{member.id}/reset-password/").status_code == 400


# ---- step 3: staff management (owner only) ----


def test_owner_creates_staff_who_can_sign_in(owner):
    response = client_for(owner).post(STAFF, {"full_name": "Tobi Ops", "email": "tobi@axiomvault.ng", "password": TEMP}, format="json")

    assert response.status_code == 201
    body = response.json()
    assert body["admin_role"] == "staff" and body["must_change_password"] is True
    created = User.objects.get(email="tobi@axiomvault.ng")
    assert created.is_staff and not created.is_superuser and created.organisation is None
    login = APIClient().post("/api/v1/admin/auth/login/", {"email": "tobi@axiomvault.ng", "password": TEMP}, format="json")
    assert login.status_code == 200 and login.json()["user"]["must_change_password"] is True
    assert AuditLog.objects.get(action=AuditLog.USER_CREATED).details["kind"] == "staff"


def test_owner_lists_edits_deactivates_staff(owner, staff):
    client = client_for(owner)

    body = client.get(STAFF).json()
    assert [r["email"] for r in body["results"]] == ["support@axiomvault.ng"]  # The owner is not listed.
    assert client.patch(f"{STAFF}{staff.id}/", {"full_name": "Kemi Adeyemi"}, format="json").json()["full_name"] == "Kemi Adeyemi"
    assert client.post(f"{STAFF}{staff.id}/deactivate/").json()["is_active"] is False
    assert APIClient().post("/api/v1/admin/auth/login/", {"email": "support@axiomvault.ng", "password": PASSWORD}, format="json").status_code == 401
    assert client.post(f"{STAFF}{staff.id}/reactivate/").json()["is_active"] is True


def test_staff_cannot_manage_staff(staff):
    response = client_for(staff).get(STAFF)

    assert response.status_code == 403 and response.json() == {"error": "Only the platform owner can manage staff accounts.", "code": "owner_only"}
    assert client_for(staff).post(STAFF, {"full_name": "X", "email": "x@axiomvault.ng", "password": TEMP}, format="json").status_code == 403


def test_owner_accounts_are_not_reachable(owner):
    other_owner = User.objects.create_superuser(username="owner2", email="owner2@axiomvault.ng", password=PASSWORD)
    client = client_for(owner)

    assert client.get(f"{STAFF}{other_owner.id}/").status_code == 404
    assert client.post(f"{STAFF}{owner.id}/deactivate/").status_code == 404


def test_cooperative_users_are_not_staff(owner, member):
    assert client_for(owner).get(f"{STAFF}{member.id}/").status_code == 404


# ---- step 4: roles ----


def test_roles_list(staff):
    body = client_for(staff).get("/api/v1/admin/roles/").json()

    assert [r["role"] for r in body["roles"]] == ["owner", "staff", "super_admin", "team_member"]
    assert all(r["permissions"] and r["panel"] in ("admin", "app") for r in body["roles"])
