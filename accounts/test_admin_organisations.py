from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken, RefreshToken

from accounts.models import AuditLog, Organisation
from fraud.models import Alert

User = get_user_model()
ORGS = "/api/v1/admin/organisations/"
PASSWORD = "Str0ng-Passw0rd!"
SUSPENDED_BODY = {"error": "Your organisation's access is suspended. Contact AxiomVault support.", "code": "organisation_suspended"}


@pytest.fixture
def staff(db):
    return User.objects.create_user(username="support", email="support@axiomvault.ng", password=PASSWORD, is_staff=True)


@pytest.fixture
def owner(db):
    return User.objects.create_superuser(username="owner", email="owner@axiomvault.ng", password=PASSWORD)


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
        username="ada", email="ada@ikejacoop.ng", password=PASSWORD, organisation=org, account_type="team_member", is_verified=True,
    )


def client_for(user):
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION="Bearer " + str(AccessToken.for_user(user)))
    return client


def suspend(org):
    Organisation.objects.filter(pk=org.pk).update(status=Organisation.SUSPENDED, suspended_at=timezone.now(), suspension_reason="test")


# ---- step 1: suspension is enforced ----


def test_a_suspended_organisations_user_cannot_log_in(boss, org):
    suspend(org)

    response = APIClient().post("/api/v1/auth/login/", {"email": "ngozi@ikejacoop.ng", "password": PASSWORD, "role": "super_admin"}, format="json")

    assert response.status_code == 403 and response.json() == SUSPENDED_BODY


def test_a_wrong_password_still_says_wrong_password(boss, org):
    suspend(org)

    response = APIClient().post("/api/v1/auth/login/", {"email": "ngozi@ikejacoop.ng", "password": "nope", "role": "super_admin"}, format="json")

    assert response.status_code == 401


def test_a_still_valid_token_is_refused_everywhere(boss, org):
    client = client_for(boss)
    assert client.get("/api/v1/me/").status_code == 200

    suspend(org)

    for path in ("/api/v1/me/", "/api/v1/team/members/", "/api/v1/fraud/alerts/", "/api/v1/dashboard/summary/"):
        response = client.get(path)
        assert response.status_code == 403 and response.json() == SUSPENDED_BODY, path


def test_refresh_is_refused(boss, org):
    refresh = str(RefreshToken.for_user(boss))
    suspend(org)

    assert APIClient().post("/api/v1/auth/token/refresh/", {"refresh": refresh}, format="json").status_code == 401


def test_staff_and_active_organisations_are_unaffected(staff, boss):
    other = Organisation.objects.create(name="Lekki Coop")
    suspend(other)

    assert client_for(boss).get("/api/v1/me/").status_code == 200
    assert client_for(staff).get(ORGS).status_code == 200


# ---- step 2: list, view, create, edit ----


def test_list_organisations(staff, boss, member, org):
    User.objects.filter(pk=member.pk).update(is_active=False)
    newer = Organisation.objects.create(name="Lekki Coop")
    Organisation.objects.filter(pk=newer.pk).update(created_at=timezone.now() + timedelta(seconds=5))

    body = client_for(staff).get(ORGS).json()

    assert body["count"] == 2 and [o["name"] for o in body["results"]] == ["Lekki Coop", "Ikeja Coop"]
    ikeja = body["results"][1]
    assert ikeja["status"] == "active" and ikeja["is_demo"] is False and ikeja["suspended_at"] is None
    assert ikeja["super_admin"] == {"id": str(boss.id), "full_name": "Ngozi Okafor", "email": "ngozi@ikejacoop.ng"}
    assert (ikeja["members_count"], ikeja["active_members_count"]) == (2, 1)
    assert body["results"][0]["super_admin"] is None and body["results"][0]["members_count"] == 0


def test_list_filters_and_search(owner, org):
    lekki = Organisation.objects.create(name="Lekki Coop")
    suspend(lekki)
    client = client_for(owner)

    assert [o["name"] for o in client.get(ORGS, {"status": "suspended"}).json()["results"]] == ["Lekki Coop"]
    assert [o["name"] for o in client.get(ORGS, {"search": "ikeja"}).json()["results"]] == ["Ikeja Coop"]
    assert client.get(ORGS, {"status": "closed"}).status_code == 400
    assert client.get(ORGS, {"page_size": 1}).json()["next"]


def test_view_an_organisation_with_activity(staff, org):
    Alert.objects.create(
        organisation=org, number=1, rule=Alert.DUPLICATE_PAYMENT, risk_type="Duplicate Payment", title="t", explanation="e",
        severity="high", risk_score=90, confidence=90, entity_type="vendor", entity_name="Acme", entity_ref="V1", fingerprint="f1",
    )

    body = client_for(staff).get(f"{ORGS}{org.id}/").json()

    assert body["name"] == "Ikeja Coop" and body["alerts_count"] == 1 and body["open_alerts_count"] == 1
    assert body["cases_count"] == 0 and body["uploads_count"] == 0 and body["last_upload_at"] is None


def test_create_an_organisation(staff, org):
    response = client_for(staff).post(ORGS, {"name": "  Lekki Coop  "}, format="json")

    assert response.status_code == 201
    created = Organisation.objects.get(name="Lekki Coop")
    assert response.json()["id"] == str(created.id) and response.json()["status"] == "active"
    row = AuditLog.objects.get(action=AuditLog.ORGANISATION_CREATED)
    assert row.actor == staff and row.organisation == created


@pytest.mark.parametrize("name", ["IKEJA COOP", "", "x" * 256])
def test_create_refusals(staff, org, name):
    assert client_for(staff).post(ORGS, {"name": name}, format="json").status_code == 400
    assert Organisation.objects.count() == 1


def test_rename_an_organisation(staff, org):
    Organisation.objects.create(name="Lekki Coop")
    client = client_for(staff)

    assert client.patch(f"{ORGS}{org.id}/", {"name": "lekki coop"}, format="json").json() == {"name": ["An organisation with this name already exists."]}
    assert client.patch(f"{ORGS}{org.id}/", {"name": "Ikeja Coop"}, format="json").status_code == 200  # Same name is fine.
    response = client.patch(f"{ORGS}{org.id}/", {"name": "Ikeja Multipurpose Coop"}, format="json")

    assert response.status_code == 200 and response.json()["name"] == "Ikeja Multipurpose Coop"
    assert AuditLog.objects.get(action=AuditLog.ORGANISATION_UPDATED).details == {"from": "Ikeja Coop", "to": "Ikeja Multipurpose Coop"}


def test_only_staff_may_use_it(boss, org):
    assert APIClient().get(ORGS).status_code == 401
    response = client_for(boss).get(ORGS)
    assert response.status_code == 403 and response.json()["code"] == "not_admin"


def test_unknown_or_bad_ids_are_404(staff):
    assert client_for(staff).get(f"{ORGS}00000000-0000-0000-0000-000000000000/").status_code == 404
    assert client_for(staff).get(f"{ORGS}not-an-id/").status_code == 404


def test_staff_on_a_temporary_password_are_blocked(staff):
    User.objects.filter(pk=staff.pk).update(must_change_password=True)
    staff.refresh_from_db()

    assert client_for(staff).get(ORGS).json()["code"] == "password_change_required"


# ---- step 3: suspend and reactivate ----


def test_suspend_signs_everyone_out(staff, boss, member, org):
    refreshes = [str(RefreshToken.for_user(u)) for u in (boss, member)]

    response = client_for(staff).post(f"{ORGS}{org.id}/suspend/", {"reason": "  Unpaid invoice  "}, format="json")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "suspended" and body["suspended_at"] and body["suspension_reason"] == "Unpaid invoice"
    for refresh in refreshes:
        assert APIClient().post("/api/v1/auth/token/refresh/", {"refresh": refresh}, format="json").status_code == 401
    assert AuditLog.objects.get(action=AuditLog.ORGANISATION_SUSPENDED).details == {"reason": "Unpaid invoice", "users_signed_out": 2}


def test_suspend_refusals(staff, org):
    client = client_for(staff)

    assert client.post(f"{ORGS}{org.id}/suspend/", {}, format="json").json() == {"reason": ["This field is required."]}
    client.post(f"{ORGS}{org.id}/suspend/", {"reason": "x"}, format="json")
    assert client.post(f"{ORGS}{org.id}/suspend/", {"reason": "again"}, format="json").json() == {"status": ["This organisation is already suspended."]}


def test_reactivate_lets_users_back_in(staff, boss, org):
    client = client_for(staff)
    client.post(f"{ORGS}{org.id}/suspend/", {"reason": "x"}, format="json")

    response = client.post(f"{ORGS}{org.id}/reactivate/")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "active" and body["suspended_at"] is None and body["suspension_reason"] == ""
    login = APIClient().post("/api/v1/auth/login/", {"email": "ngozi@ikejacoop.ng", "password": PASSWORD, "role": "super_admin"}, format="json")
    assert login.status_code == 200
    assert AuditLog.objects.get(action=AuditLog.ORGANISATION_REACTIVATED).details == {"previous_reason": "x"}
    assert client.post(f"{ORGS}{org.id}/reactivate/").json() == {"status": ["This organisation is already active."]}
