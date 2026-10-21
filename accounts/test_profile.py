import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from accounts.models import AuditLog, Organisation

User = get_user_model()
ME = "/api/v1/me/"


@pytest.fixture
def org(db):
    return Organisation.objects.create(name="Ikeja Coop")


@pytest.fixture
def owner(org):
    return User.objects.create_user(
        username="owner", email="owner@ikejacoop.ng", password="Str0ng-Passw0rd!", full_name="Ngozi Okafor",
        organisation=org, account_type="super_admin", department="internal_audit", is_verified=True,
    )


@pytest.fixture
def member(org):
    return User.objects.create_user(
        username="ada", email="ada@ikejacoop.ng", password="Str0ng-Passw0rd!", full_name="Ada Obi", organisation=org,
        account_type="team_member", role="Payroll Analyst", phone_number="+2348031234567", is_verified=True,
    )


def client_for(user):
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION="Bearer " + str(AccessToken.for_user(user)))
    return client


def test_read_my_profile(owner, org):
    body = client_for(owner).get(ME).json()

    assert body == {
        "id": str(owner.id), "email": "owner@ikejacoop.ng", "full_name": "Ngozi Okafor", "department": "internal_audit",
        "phone_number": "", "role": "", "account_type": "super_admin", "organisation": {"id": str(org.id), "name": "Ikeja Coop"},
        "profile_picture": None, "is_staff": False, "date_joined": body["date_joined"], "last_login": None,
    }


def test_a_team_member_and_staff_read_theirs(member, db):
    body = client_for(member).get(ME).json()
    assert (body["account_type"], body["role"], body["phone_number"]) == ("team_member", "Payroll Analyst", "+2348031234567")

    staff = User.objects.create_user(username="staff", email="staff@axiomvault.ng", password="Str0ng-Passw0rd!", is_staff=True, is_verified=True)
    body = client_for(staff).get(ME).json()
    assert body["organisation"] is None and body["account_type"] == "" and body["is_staff"] is True


def test_edit_my_profile(owner):
    response = client_for(owner).patch(
        ME, {"full_name": "  Ngozi A. Okafor ", "department": "compliance", "phone_number": "+234 803-123-4567"}, format="json"
    )

    assert response.status_code == 200
    body = response.json()
    assert (body["full_name"], body["department"], body["phone_number"]) == ("Ngozi A. Okafor", "compliance", "+2348031234567")
    owner.refresh_from_db()
    assert owner.full_name == "Ngozi A. Okafor" and owner.phone_number == "+2348031234567"
    assert AuditLog.objects.get(action=AuditLog.PROFILE_UPDATED).details == {"fields": ["full_name", "department", "phone_number"]}


def test_only_sent_fields_change_and_read_only_keys_are_ignored(member):
    response = client_for(member).patch(
        ME, {"department": "finance", "email": "new@evil.ng", "role": "Boss", "account_type": "super_admin", "is_staff": True}, format="json"
    )

    assert response.status_code == 200
    member.refresh_from_db()
    assert member.department == "finance" and member.full_name == "Ada Obi"
    assert (member.email, member.role, member.account_type, member.is_staff) == ("ada@ikejacoop.ng", "Payroll Analyst", "team_member", False)


def test_empty_phone_clears_it(member):
    assert client_for(member).patch(ME, {"phone_number": ""}, format="json").json()["phone_number"] == ""
    member.refresh_from_db()
    assert member.phone_number == ""


def test_no_change_writes_no_audit_row(owner):
    response = client_for(owner).patch(ME, {"full_name": "Ngozi Okafor"}, format="json")

    assert response.status_code == 200 and not AuditLog.objects.filter(action=AuditLog.PROFILE_UPDATED).exists()


@pytest.mark.parametrize(
    "body, field",
    [({"full_name": "   "}, "full_name"), ({"full_name": "x" * 256}, "full_name"), ({"department": "sales"}, "department"), ({"phone_number": "12ab"}, "phone_number")],
)
def test_edit_refusals(owner, body, field):
    response = client_for(owner).patch(ME, body, format="json")

    assert response.status_code == 400 and field in response.json()
    owner.refresh_from_db()
    assert owner.full_name == "Ngozi Okafor" and owner.department == "internal_audit"


def test_profile_needs_a_token_and_a_changed_password(member):
    assert APIClient().get(ME).status_code == 401
    assert APIClient().patch(ME, {}, format="json").status_code == 401
    User.objects.filter(pk=member.pk).update(must_change_password=True)
    member.refresh_from_db()
    assert client_for(member).get(ME).status_code == 403
    assert client_for(member).patch(ME, {"full_name": "X"}, format="json").status_code == 403
