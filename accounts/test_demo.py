from io import StringIO

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from rest_framework.test import APIClient

from accounts.demo import ADMIN_EMAIL, DEMO_ORGANISATION, demo_counts, seed
from accounts.models import AuditLog, Organisation
from fraud.models import Alert, AlertEvent, AlertNote, Case, CaseComment, CaseEvent
from ingestion.models import DataUpload, Payment, Payroll, Vendor

User = get_user_model()


def run(command, *args):
    out = StringIO()
    call_command(command, *args, stdout=out)
    return out.getvalue()


@pytest.mark.django_db
def test_seed_builds_every_screen():
    output = run("seed_demo", "--password", "Demo-Pass-2026!")

    organisation = Organisation.objects.get(name=DEMO_ORGANISATION)
    assert organisation.is_demo is True
    assert "created" in output and ADMIN_EMAIL in output
    assert User.objects.filter(organisation=organisation).count() == 5
    assert User.objects.get(email="ibrahim.musa@axiomvault.demo").is_active is False
    assert User.objects.get(email="chioma.eze@axiomvault.demo").must_change_password is True
    assert set(DataUpload.objects.filter(organisation=organisation).values_list("kind", flat=True)) == {"payroll", "vendors", "payments"}
    assert Payroll.objects.filter(organisation=organisation).count() == 80
    assert Vendor.objects.filter(organisation=organisation).count() == 8
    assert Payment.objects.filter(organisation=organisation).count() == 26

    alerts = Alert.objects.filter(organisation=organisation)
    assert alerts.count() == 5
    assert set(alerts.values_list("rule", flat=True)) == {"duplicate_bank_account", "duplicate_payment"}
    assert set(alerts.values_list("status", flat=True)) == {"new", "investigating", "escalated", "false_positive"}
    assert set(alerts.values_list("severity", flat=True)) == {"critical", "high", "medium", "low"}
    assert alerts.exclude(assigned_reviewer=None).count() == 3
    assert AlertNote.objects.filter(alert__organisation=organisation).count() == 3
    assert AlertEvent.objects.filter(alert__organisation=organisation, type="status_changed").count() == 3
    assert alerts.filter(entity_name="Phantom Solutions LLC", severity="critical").exists()

    cases = Case.objects.filter(organisation=organisation)
    assert set(cases.values_list("status", flat=True)) == {"investigating", "escalated"}
    assert {c.reference for c in cases} == {"CASE-0001", "CASE-0002"}
    assert alerts.exclude(case=None).count() == 2
    assert CaseComment.objects.filter(case__organisation=organisation).count() == 2
    assert cases.get(status="escalated").escalation_reason == "legal_risk"


@pytest.mark.django_db
def test_demo_admin_can_log_in():
    run("seed_demo", "--password", "Demo-Pass-2026!")

    response = APIClient().post(
        "/api/v1/auth/login/", {"email": ADMIN_EMAIL, "password": "Demo-Pass-2026!", "role": "super_admin"}, format="json"
    )

    assert response.status_code == 200
    assert response.json()["user"]["organization"] == DEMO_ORGANISATION


@pytest.mark.django_db
def test_seed_twice_changes_nothing():
    run("seed_demo")
    before = demo_counts()

    output = run("seed_demo")

    assert "already exists" in output
    assert demo_counts() == before


@pytest.mark.django_db
def test_reset_rebuilds_the_demo():
    first = seed()["organisation"].pk

    output = run("seed_demo", "--reset")

    assert "created" in output
    assert Organisation.objects.filter(is_demo=True).count() == 1
    assert Organisation.objects.get(is_demo=True).pk != first
    assert Alert.objects.count() == 5


@pytest.mark.django_db
def test_clear_without_yes_deletes_nothing():
    seed()
    before = demo_counts()

    output = run("clear_demo")

    assert "Nothing deleted" in output
    assert demo_counts() == before


@pytest.mark.django_db
def test_clear_removes_only_demo_data():
    real = Organisation.objects.create(name="Ikeja Coop")
    owner = User.objects.create_user(username="owner", email="owner@ikejacoop.ng", password="Str0ng-Passw0rd!", organisation=real, account_type="super_admin")
    real_upload = DataUpload.objects.create(organisation=real, kind="payments", file_name="p.csv", row_count=0)
    AuditLog.record(AuditLog.LOGIN, actor=owner)
    seed()

    output = run("clear_demo", "--yes")

    assert "deleted" in output
    assert not Organisation.objects.filter(is_demo=True).exists()
    assert not User.objects.filter(email__endswith="@axiomvault.demo").exists()
    assert set(DataUpload.objects.values_list("pk", flat=True)) == {real_upload.pk}
    assert Payroll.objects.count() == Vendor.objects.count() == Payment.objects.count() == Alert.objects.count() == 0
    assert AlertEvent.objects.count() == AlertNote.objects.count() == 0
    assert Case.objects.count() == CaseEvent.objects.count() == CaseComment.objects.count() == 0
    assert list(AuditLog.objects.values_list("actor_email", flat=True)) == ["owner@ikejacoop.ng"]
    assert Organisation.objects.filter(pk=real.pk).exists() and User.objects.filter(pk=owner.pk).exists()


@pytest.mark.django_db
def test_clear_with_no_demo_is_harmless():
    assert "No demo data found" in run("clear_demo", "--yes")
