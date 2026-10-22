import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from accounts.models import AuditLog, Organisation, UserSettings

User = get_user_model()
SETTINGS = "/api/v1/me/settings/"
DEFAULTS = {
    "critical_risk_alerts": True, "high_risk_alerts": True, "daily_summary_reports": False, "system_updates": True,
    "language": "en-US", "time_zone": "Africa/Lagos", "date_format": "DD/MM/YYYY",
}


@pytest.fixture
def org(db):
    return Organisation.objects.create(name="Ikeja Coop")


@pytest.fixture
def owner(org):
    return User.objects.create_user(
        username="owner", email="owner@ikejacoop.ng", password="Str0ng-Passw0rd!", organisation=org,
        account_type="super_admin", is_verified=True,
    )


@pytest.fixture
def member(org):
    return User.objects.create_user(
        username="ada", email="ada@ikejacoop.ng", password="Str0ng-Passw0rd!", organisation=org,
        account_type="team_member", is_verified=True,
    )


def client_for(user):
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION="Bearer " + str(AccessToken.for_user(user)))
    return client


def test_first_read_creates_the_defaults(owner):
    body = client_for(owner).get(SETTINGS).json()

    assert {k: v for k, v in body.items() if k != "updated_at"} == DEFAULTS and body["updated_at"]
    assert UserSettings.objects.filter(user=owner).count() == 1
    client_for(owner).get(SETTINGS)
    assert UserSettings.objects.count() == 1


def test_save_changes_only_what_is_sent(owner):
    response = client_for(owner).patch(
        SETTINGS, {"daily_summary_reports": True, "language": "en-GB", "time_zone": "Europe/London", "date_format": "YYYY-MM-DD", "user": 999},
        format="json",
    )

    assert response.status_code == 200
    body = response.json()
    assert body["daily_summary_reports"] is True and body["language"] == "en-GB"
    assert body["time_zone"] == "Europe/London" and body["date_format"] == "YYYY-MM-DD"
    assert body["critical_risk_alerts"] is True and body["system_updates"] is True
    saved = UserSettings.objects.get(user=owner)
    assert (saved.daily_summary_reports, saved.language) == (True, "en-GB")
    assert AuditLog.objects.get(action=AuditLog.SETTINGS_UPDATED).details == {"fields": ["daily_summary_reports", "language", "time_zone", "date_format"]}


def test_switches_turn_off(owner):
    body = client_for(owner).patch(SETTINGS, {"critical_risk_alerts": False, "system_updates": False}, format="json").json()

    assert body["critical_risk_alerts"] is False and body["system_updates"] is False


def test_no_change_writes_no_audit_row(owner):
    client_for(owner).patch(SETTINGS, {"language": "en-US"}, format="json")

    assert not AuditLog.objects.filter(action=AuditLog.SETTINGS_UPDATED).exists()


@pytest.mark.parametrize(
    "body, field",
    [
        ({"language": "fr"}, "language"),
        ({"date_format": "DD-MM-YY"}, "date_format"),
        ({"time_zone": "Lagos"}, "time_zone"),
        ({"time_zone": "../../etc/passwd"}, "time_zone"),
        ({"system_updates": "maybe"}, "system_updates"),
        ({"language": "en-GB", "time_zone": "Mars/Base"}, "time_zone"),
    ],
)
def test_bad_values_save_nothing(owner, body, field):
    response = client_for(owner).patch(SETTINGS, body, format="json")

    assert response.status_code == 400 and field in response.json()
    assert UserSettings.objects.get(user=owner).language == "en-US"


def test_each_user_has_their_own(owner, member):
    client_for(owner).patch(SETTINGS, {"language": "es"}, format="json")

    assert client_for(member).get(SETTINGS).json()["language"] == "en-US"
    assert client_for(owner).get(SETTINGS).json()["language"] == "es"


def test_staff_have_settings_too(db):
    staff = User.objects.create_user(username="staff", email="staff@axiomvault.ng", password="Str0ng-Passw0rd!", is_staff=True, is_verified=True)

    assert client_for(staff).patch(SETTINGS, {"date_format": "MM/DD/YYYY"}, format="json").json()["date_format"] == "MM/DD/YYYY"


def test_settings_need_a_token_and_a_changed_password(member):
    assert APIClient().get(SETTINGS).status_code == 401
    assert APIClient().patch(SETTINGS, {}, format="json").status_code == 401
    User.objects.filter(pk=member.pk).update(must_change_password=True)
    member.refresh_from_db()
    assert client_for(member).get(SETTINGS).status_code == 403
    assert not UserSettings.objects.exists()
