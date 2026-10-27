from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from accounts.models import AuditLog, Organisation
from fraud.models import Alert
from fraud.services.detection import create_alerts, duplicate_bank_accounts, duplicate_payments, run_detection
from ingestion.models import DataUpload, Payment, Payroll, Vendor

User = get_user_model()
PAYROLL_URL = "/api/v1/ingestion/upload-payroll/"
PAYMENTS_URL = "/api/v1/ingestion/upload-payments/"
VENDORS_URL = "/api/v1/ingestion/upload-vendors/"


@pytest.fixture
def org(db):
    return Organisation.objects.create(name="Ikeja Coop")


def make_upload(organisation, kind):
    return DataUpload.objects.create(organisation=organisation, kind=kind, file_name=f"{kind}.csv", row_count=0)


def payroll(upload, employee_id, account="0123456789", period="2026-09", name=None, net="230000"):
    return Payroll.objects.create(
        organisation=upload.organisation, upload=upload, employee_id=employee_id,
        employee_name=name or f"Employee {employee_id}", department="Operations", salary_basic=Decimal("250000"),
        net_salary=Decimal(net), account_number=account, pay_period=period, payment_date=date(2026, 9, 28),
    )


def payment(upload, txn, vendor="VND-8847", invoice="INV-1", amount="42350", day=5, approver="John Doe"):
    return Payment.objects.create(
        organisation=upload.organisation, upload=upload, transaction_id=txn, vendor_id=vendor, invoice_number=invoice,
        amount=Decimal(amount), payment_date=date(2026, 5, day), approver=approver, department="Procurement",
    )


# ---- 8b: duplicate bank account ----


def test_shared_account_in_one_period_is_a_finding(org):
    upload = make_upload(org, DataUpload.PAYROLL)
    payroll(upload, "EMP002", name="Bola Ade")
    payroll(upload, "EMP001", name="Ada Obi")
    payroll(upload, "EMP003", account="0999999999")

    [finding] = duplicate_bank_accounts(org, upload)

    assert finding["severity"] == "high" and finding["risk_score"] == 85 == finding["confidence"]
    assert finding["entity_type"] == "employee" and finding["entity_ref"] == "EMP001" and finding["entity_name"] == "Ada Obi"
    assert [r["employee_id"] for r in finding["flagged_records"]] == ["EMP001", "EMP002"]
    assert finding["flagged_records"][0]["net_salary"] == "230,000.00"
    assert finding["fingerprint"] == "duplicate_bank_account:2026-09:0123456789"
    assert "0123456789" in finding["explanation"] and "Ada Obi" in finding["explanation"]


def test_three_employees_on_one_account_is_critical(org):
    upload = make_upload(org, DataUpload.PAYROLL)
    for emp in ("E1", "E2", "E3"):
        payroll(upload, emp)

    [finding] = duplicate_bank_accounts(org, upload)

    assert finding["severity"] == "critical" and finding["risk_score"] == 95


@pytest.mark.parametrize(
    "rows",
    [
        [("E1", "0123456789", "2026-09"), ("E2", "0999999999", "2026-09")],  # different accounts
        [("E1", "0123456789", "2026-09"), ("E2", "0123456789", "2026-10")],  # different periods
        [("E1", "0123456789", "2026-09"), ("E1", "0123456789", "2026-09")],  # same employee twice
    ],
)
def test_no_finding_when_nothing_is_shared(org, rows):
    upload = make_upload(org, DataUpload.PAYROLL)
    for emp, account, period in rows:
        payroll(upload, emp, account=account, period=period)

    assert duplicate_bank_accounts(org, upload) == []


def test_shared_account_across_two_uploads_is_found(org):
    first = make_upload(org, DataUpload.PAYROLL)
    payroll(first, "E1")
    second = make_upload(org, DataUpload.PAYROLL)
    payroll(second, "E2")

    assert len(duplicate_bank_accounts(org, second)) == 1


def test_other_organisations_rows_are_never_read(org):
    other = Organisation.objects.create(name="Lekki Coop")
    payroll(make_upload(other, DataUpload.PAYROLL), "E9")
    upload = make_upload(org, DataUpload.PAYROLL)
    payroll(upload, "E1")

    assert duplicate_bank_accounts(org, upload) == []


# ---- 8b: duplicate payment ----


def test_invoice_paid_twice_is_a_finding_with_vendor_name(org):
    vendors = make_upload(org, DataUpload.VENDORS)
    Vendor.objects.create(organisation=org, upload=vendors, vendor_id="VND-8847", vendor_name="Acme Supplies", account_number="1")
    upload = make_upload(org, DataUpload.PAYMENTS)
    payment(upload, "TXN-90455", day=8)
    payment(upload, "TXN-90214", day=5)

    [finding] = duplicate_payments(org, upload)

    assert finding["severity"] == "high" and finding["risk_score"] == 90
    assert finding["entity_type"] == "vendor" and finding["entity_name"] == "Acme Supplies" and finding["entity_ref"] == "VND-8847"
    assert [r["transaction_id"] for r in finding["flagged_records"]] == ["TXN-90214", "TXN-90455"]
    assert finding["flagged_records"][0] == {
        "transaction_id": "TXN-90214", "payment_date": "2026-05-05", "amount": "42,350.00",
        "invoice_number": "INV-1", "approver": "John Doe",
    }
    assert [e["label"] for e in finding["timeline"]] == ["Payment", "Payment", "Alert generated"]
    assert "42,350.00 is more than the invoice should have cost" in finding["explanation"]


def test_vendor_without_a_vendors_file_uses_its_id(org):
    upload = make_upload(org, DataUpload.PAYMENTS)
    payment(upload, "T1")
    payment(upload, "T2")

    assert duplicate_payments(org, upload)[0]["entity_name"] == "VND-8847"


@pytest.mark.parametrize(
    "amounts, expected",
    [(["600000", "600000"], ("high", 90)), (["1000000", "1000000"], ("critical", 98)), (["10", "10", "10"], ("critical", 98))],
)
def test_duplicate_payment_severity(org, amounts, expected):
    upload = make_upload(org, DataUpload.PAYMENTS)
    for index, amount in enumerate(amounts):
        payment(upload, f"T{index}", amount=amount)

    finding = duplicate_payments(org, upload)[0]

    assert (finding["severity"], finding["risk_score"]) == expected


def test_different_invoices_or_vendors_are_not_duplicates(org):
    upload = make_upload(org, DataUpload.PAYMENTS)
    payment(upload, "T1", invoice="INV-1")
    payment(upload, "T2", invoice="INV-2")
    payment(upload, "T3", vendor="VND-1", invoice="INV-1")

    assert duplicate_payments(org, upload) == []


# ---- 8d: alerts ----


def test_alerts_are_numbered_per_organisation_and_audited(org):
    upload = make_upload(org, DataUpload.PAYMENTS)
    for invoice in ("INV-1", "INV-2"):
        payment(upload, f"{invoice}-a", invoice=invoice)
        payment(upload, f"{invoice}-b", invoice=invoice)
    other = Organisation.objects.create(name="Lekki Coop")
    other_upload = make_upload(other, DataUpload.PAYMENTS)
    payment(other_upload, "X1")
    payment(other_upload, "X2")

    alerts = run_detection(upload)
    [other_alert] = run_detection(other_upload)

    assert [a.reference for a in alerts] == ["ALT-0001", "ALT-0002"]
    assert other_alert.reference == "ALT-0001"
    alert = Alert.objects.get(organisation=org, number=1)
    assert alert.status == "new" and alert.confidence_source == "derived" and alert.currency == "NGN"
    assert alert.upload == upload and alert.ai_summary == "" and alert.ai_confidence is None
    audit = AuditLog.objects.filter(action=AuditLog.ALERT_CREATED, organisation=org)
    assert audit.count() == 2 and audit.first().actor is None
    assert {a.details["reference"] for a in audit} == {"ALT-0001", "ALT-0002"}


def test_the_same_finding_never_opens_twice(org):
    upload = make_upload(org, DataUpload.PAYMENTS)
    payment(upload, "T1")
    payment(upload, "T2")
    assert len(run_detection(upload)) == 1

    again = make_upload(org, DataUpload.PAYMENTS)
    payment(again, "T3")

    assert run_detection(again) == []
    assert Alert.objects.filter(organisation=org).count() == 1


def test_numbering_continues_after_existing_alerts(org):
    first = make_upload(org, DataUpload.PAYMENTS)
    payment(first, "A1", invoice="INV-A")
    payment(first, "A2", invoice="INV-A")
    run_detection(first)
    second = make_upload(org, DataUpload.PAYMENTS)
    payment(second, "B1", invoice="INV-B")
    payment(second, "B2", invoice="INV-B")

    assert [a.reference for a in run_detection(second)] == ["ALT-0002"]


def test_reference_grows_past_four_digits(org):
    alert = create_alerts(org, [], None)
    assert alert == []
    assert Alert(number=12345).reference == "ALT-12345"


# ---- 8c: run after upload ----


@pytest.fixture
def uploader(org):
    return User.objects.create_user(
        username="owner", email="owner@ikejacoop.ng", password="Str0ng-Passw0rd!", organisation=org,
        account_type="super_admin", is_verified=True,
    )


def client_for(user):
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION="Bearer " + str(AccessToken.for_user(user)))
    return client


def csv(name, header, rows):
    text = "\n".join([header] + rows) + "\n"
    return SimpleUploadedFile(name, text.encode("utf-8"))


PAYROLL_HEADER = "employee_id,employee_name,department,salary_basic,net_salary,account_number,pay_period,payment_date,role,approver_id,attendance_days,employment_status"
PAYMENTS_HEADER = "transaction_id,vendor_id,invoice_number,amount,payment_date"


def test_payroll_upload_reports_new_alerts(uploader):
    file = csv("p.csv", PAYROLL_HEADER, [
        "E1,Ada,Ops,100,90,0123456789,2026-09,2026-09-28,Clerk,APR-01,22,Active",
        "E2,Bola,Ops,100,90,0123456789,2026-09,2026-09-28,Clerk,APR-01,22,Active",
    ])

    body = client_for(uploader).post(PAYROLL_URL, {"pay_period_start": "2026-01-01", "pay_period_end": "2026-12-31", "file": file}, format="multipart").json()

    assert body["detection"] == {"status": "completed", "alerts_created": 1, "alerts": ["ALT-0001"]}
    assert Alert.objects.get().rule == Alert.DUPLICATE_BANK_ACCOUNT


def test_payments_upload_reports_new_alerts(uploader):
    file = csv("pay.csv", PAYMENTS_HEADER, ["T1,V1,INV-1,100,2026-05-05", "T2,V1,INV-1,100,2026-05-08"])

    body = client_for(uploader).post(PAYMENTS_URL, {"file": file}, format="multipart").json()

    assert body["detection"]["alerts"] == ["ALT-0001"]


def test_clean_upload_reports_zero_alerts(uploader):
    file = csv("p.csv", PAYROLL_HEADER, ["E1,Ada,Ops,100,90,0123456789,2026-09,2026-09-28,Clerk,APR-01,22,Active"])

    body = client_for(uploader).post(PAYROLL_URL, {"pay_period_start": "2026-01-01", "pay_period_end": "2026-12-31", "file": file}, format="multipart").json()

    assert body["detection"] == {"status": "completed", "alerts_created": 0, "alerts": []}


def test_vendors_upload_has_no_detection(uploader):
    file = csv("v.csv", "vendor_id,vendor_name,account_number", ["V1,Acme,0123"])

    body = client_for(uploader).post(VENDORS_URL, {"file": file}, format="multipart").json()

    assert body["detection"] is None


def test_detection_error_never_undoes_the_upload(uploader):
    file = csv("pay.csv", PAYMENTS_HEADER, ["T1,V1,INV-1,100,2026-05-05", "T2,V1,INV-1,100,2026-05-08"])

    with patch("ingestion.viewsets.run_detection", side_effect=RuntimeError("rule crashed")):
        response = client_for(uploader).post(PAYMENTS_URL, {"file": file}, format="multipart")

    assert response.status_code == 201
    assert response.json()["detection"] == {"status": "failed", "alerts_created": 0, "alerts": []}
    assert Payment.objects.count() == 2 and Alert.objects.count() == 0


# ---- 9: alert review ----

from fraud.models import AlertEvent, AlertNote

ALERTS_URL = "/api/v1/fraud/alerts/"


def alert_url(reference, action=""):
    return f"{ALERTS_URL}{reference}/" + (f"{action}/" if action else "")


def make_alert(organisation, number, **overrides):
    values = {
        "rule": Alert.DUPLICATE_PAYMENT, "risk_type": "Duplicate Payment", "title": f"Alert {number}",
        "explanation": "Paid twice.", "severity": "high", "risk_score": 90, "confidence": 90, "entity_type": "vendor",
        "entity_name": "Acme Supplies", "entity_ref": "VND-8847", "department": "Procurement",
        "fingerprint": f"test:{number}",
    }
    values.update(overrides)
    alert = Alert.objects.create(organisation=organisation, number=number, **values)
    AlertEvent.objects.create(alert=alert, type=AlertEvent.CREATED, to_status=alert.status, detail=alert.title)
    return alert


@pytest.fixture
def member(org):
    return User.objects.create_user(
        username="ada", email="ada@ikejacoop.ng", password="Str0ng-Passw0rd!", full_name="Ada Obi", organisation=org,
        account_type="team_member", is_verified=True,
    )


def test_detection_records_a_created_event(org):
    upload = make_upload(org, DataUpload.PAYMENTS)
    payment(upload, "T1")
    payment(upload, "T2")

    [alert] = run_detection(upload)

    assert list(alert.events.values_list("type", "to_status")) == [("created", "new")]


def test_list_is_paginated_scoped_and_newest_first(uploader, org):
    for number in range(1, 24):
        make_alert(org, number)
    make_alert(Organisation.objects.create(name="Lekki Coop"), 1, title="Not mine")

    first = client_for(uploader).get(ALERTS_URL).json()
    second = client_for(uploader).get(ALERTS_URL, {"page": 2}).json()

    assert first["count"] == 23 and len(first["results"]) == 20 and len(second["results"]) == 3
    assert first["results"][0]["reference"] == "ALT-0023"
    assert set(first["results"][0]) == {
        "reference", "risk_type", "rule", "title", "entity_type", "entity_name", "entity_ref", "department",
        "severity", "status", "risk_score", "confidence", "assigned_reviewer", "detected_at",
    }
    assert "Not mine" not in {a["title"] for a in first["results"] + second["results"]}


def test_list_filters_and_search(uploader, org, member):
    make_alert(org, 1, severity="critical", status="new", department="Finance", entity_name="Phantom Solutions LLC")
    make_alert(org, 2, severity="high", status="investigating", rule=Alert.DUPLICATE_BANK_ACCOUNT,
               risk_type="Duplicate Bank Account", entity_type="employee", entity_name="Ada Obi", entity_ref="EMP001")
    make_alert(org, 3, severity="high", status="false_positive", assigned_reviewer=member)
    client = client_for(uploader)

    def refs(**params):
        return [a["reference"] for a in client.get(ALERTS_URL, params).json()["results"]]

    assert refs(status="new") == ["ALT-0001"]
    assert refs(severity="high") == ["ALT-0003", "ALT-0002"]
    assert refs(rule="duplicate_bank_account") == ["ALT-0002"]
    assert refs(department="finance") == ["ALT-0001"]
    assert refs(reviewer=str(member.id)) == ["ALT-0003"]
    assert refs(reviewer="none") == ["ALT-0002", "ALT-0001"]
    assert refs(search="phantom") == ["ALT-0001"]
    assert refs(search="emp001") == ["ALT-0002"]
    assert refs(search="alt-0003") == ["ALT-0003"]
    assert refs(search="bank account") == ["ALT-0002"]
    assert refs(severity="high", status="investigating") == ["ALT-0002"]


@pytest.mark.parametrize("params, field", [({"status": "closed"}, "status"), ({"severity": "extreme"}, "severity"), ({"reviewer": "bob"}, "reviewer")])
def test_list_rejects_unknown_filter_values(uploader, params, field):
    response = client_for(uploader).get(ALERTS_URL, params)

    assert response.status_code == 400 and field in response.json()


def test_detail_by_reference_in_any_letter_case(uploader, org):
    upload = make_upload(org, DataUpload.PAYMENTS)
    make_alert(org, 7, upload=upload, flagged_records=[{"transaction_id": "T1"}], timeline=[{"date": "2026-05-05", "label": "Payment", "detail": "x"}])

    body = client_for(uploader).get(alert_url("alt-0007")).json()

    assert body["reference"] == "ALT-0007" and body["explanation"] == "Paid twice."
    assert body["flagged_records"] == [{"transaction_id": "T1"}] and body["evidence"] == []
    assert body["upload"]["id"] == str(upload.id) and body["upload"]["kind"] == "payments"
    assert body["currency"] == "NGN" and body["confidence_source"] == "derived" and body["notes_count"] == 0
    assert body["ai_summary"] == "" and body["ai_recommendations"] == [] and body["ai_confidence"] is None


@pytest.mark.parametrize("reference", ["ALT-0099", "ALT-1", "XYZ", "ALT-00a1"])
def test_unknown_or_malformed_references_are_404(uploader, org, reference):
    make_alert(org, 1)

    assert client_for(uploader).get(alert_url(reference)).status_code == 404


def test_other_organisations_alert_is_404(uploader):
    make_alert(Organisation.objects.create(name="Lekki Coop"), 1)

    assert client_for(uploader).get(alert_url("ALT-0001")).status_code == 404


def test_alerts_need_a_token_and_a_changed_password(org, member):
    assert APIClient().get(ALERTS_URL).status_code == 401
    User.objects.filter(pk=member.pk).update(must_change_password=True)
    member.refresh_from_db()

    assert client_for(member).get(ALERTS_URL).json()["code"] == "password_change_required"


def test_team_member_can_review_alerts(org, member):
    make_alert(org, 1)

    assert client_for(member).get(ALERTS_URL).json()["count"] == 1


@pytest.mark.parametrize("new_status", ["investigating", "escalated", "false_positive"])
def test_status_change_records_history_and_audit(uploader, org, new_status):
    make_alert(org, 1)

    body = client_for(uploader).post(alert_url("ALT-0001", "status"), {"status": new_status, "note": "  Checked.  "}, format="json").json()

    assert body["status"] == new_status
    event = AlertEvent.objects.get(type="status_changed")
    assert (event.from_status, event.to_status, event.detail, event.actor) == ("new", new_status, "Checked.", uploader)
    audit = AuditLog.objects.get(action=AuditLog.ALERT_STATUS_CHANGED)
    assert audit.details == {"reference": "ALT-0001", "from": "new", "to": new_status} and audit.actor == uploader


def test_a_false_positive_can_be_reopened(uploader, org):
    make_alert(org, 1, status="false_positive")

    assert client_for(uploader).post(alert_url("ALT-0001", "status"), {"status": "new"}, format="json").json()["status"] == "new"


def test_status_change_rejects_same_or_unknown_status(uploader, org):
    make_alert(org, 1)
    client = client_for(uploader)

    same = client.post(alert_url("ALT-0001", "status"), {"status": "new"}, format="json")
    unknown = client.post(alert_url("ALT-0001", "status"), {"status": "closed"}, format="json")

    assert same.status_code == 400 and same.json() == {"status": ["The alert is already New."]}
    assert unknown.status_code == 400
    assert not AlertEvent.objects.filter(type="status_changed").exists()


def test_history_is_oldest_first_and_names_the_actor(uploader, org):
    make_alert(org, 1)
    AlertEvent.objects.update(created_at=timezone.now() - timedelta(minutes=1))
    client = client_for(uploader)
    client.post(alert_url("ALT-0001", "status"), {"status": "investigating"}, format="json")

    results = client.get(alert_url("ALT-0001", "history")).json()["results"]

    assert [e["type"] for e in results] == ["created", "status_changed"]
    assert results[0]["actor"] is None
    assert results[1]["actor"] == {"id": str(uploader.id), "full_name": uploader.full_name, "profile_picture": None}


def test_assign_and_remove_a_reviewer(uploader, org, member):
    make_alert(org, 1)
    client = client_for(uploader)

    assigned = client.put(alert_url("ALT-0001", "reviewer"), {"reviewer_id": str(member.id)}, format="json").json()
    removed = client.put(alert_url("ALT-0001", "reviewer"), {"reviewer_id": None}, format="json").json()

    assert assigned["assigned_reviewer"] == {"id": str(member.id), "full_name": "Ada Obi", "email": "ada@ikejacoop.ng", "profile_picture": None}
    assert removed["assigned_reviewer"] is None
    assert list(AlertEvent.objects.filter(type__startswith="reviewer").values_list("type", "detail")) == [
        ("reviewer_assigned", "Ada Obi"), ("reviewer_removed", "")
    ]
    assert AuditLog.objects.filter(action=AuditLog.ALERT_REVIEWER_CHANGED).count() == 2


def test_reviewer_must_be_an_active_member_of_the_organisation(uploader, org, member):
    make_alert(org, 1)
    outsider = User.objects.create_user(username="x", email="x@lekki.ng", password="Str0ng-Passw0rd!",
                                        organisation=Organisation.objects.create(name="Lekki Coop"))
    User.objects.filter(pk=member.pk).update(is_active=False)
    client = client_for(uploader)

    for reviewer_id in (str(outsider.id), str(member.id), "3f2a7c9e-1b4d-4e8a-9c2f-6d5e4b3a2c1d"):
        response = client.put(alert_url("ALT-0001", "reviewer"), {"reviewer_id": reviewer_id}, format="json")
        assert response.status_code == 400
        assert response.json() == {"reviewer_id": ["Choose an active member of your organisation."]}
    assert Alert.objects.get().assigned_reviewer is None


def test_notes_are_added_and_listed_newest_first(uploader, org):
    make_alert(org, 1)
    client = client_for(uploader)

    first = client.post(alert_url("ALT-0001", "notes"), {"text": "  Check approvals.  "}, format="json")
    # Windows' clock ticks every ~15 ms, so give the first note a clearly earlier time.
    AlertNote.objects.update(created_at=timezone.now() - timedelta(minutes=1))
    client.post(alert_url("ALT-0001", "notes"), {"text": "Escalate if confirmed."}, format="json")
    listed = client.get(alert_url("ALT-0001", "notes")).json()

    assert first.status_code == 201
    assert first.json()["text"] == "Check approvals." and first.json()["author"]["id"] == str(uploader.id)
    assert [n["text"] for n in listed["results"]] == ["Escalate if confirmed.", "Check approvals."]
    assert client.get(alert_url("ALT-0001")).json()["notes_count"] == 2
    assert AlertEvent.objects.filter(type="note_added").count() == 2
    assert AuditLog.objects.filter(action=AuditLog.ALERT_NOTE_ADDED).count() == 2


@pytest.mark.parametrize("text, message", [("   ", "This field may not be blank."), ("x" * 2001, "Ensure this field has no more than 2000 characters.")])
def test_notes_reject_empty_or_long_text(uploader, org, text, message):
    make_alert(org, 1)

    response = client_for(uploader).post(alert_url("ALT-0001", "notes"), {"text": text}, format="json")

    assert response.status_code == 400 and response.json() == {"text": [message]}
    assert AlertNote.objects.count() == 0


def test_notes_on_another_organisations_alert_are_404(uploader):
    make_alert(Organisation.objects.create(name="Lekki Coop"), 1)

    assert client_for(uploader).post(alert_url("ALT-0001", "notes"), {"text": "hi"}, format="json").status_code == 404


# ---- 15: dashboard and analytics ----

from fraud.dashboard import exposure_level

DASH = "/api/v1/dashboard/"


def aged(alert, days_ago):
    Alert.objects.filter(pk=alert.pk).update(detected_at=timezone.now() - timedelta(days=days_ago))
    return alert


def test_summary_kpis(uploader, org):
    make_alert(org, 1, severity="critical", status="new", risk_score=98)
    make_alert(org, 2, severity="critical", status="false_positive", risk_score=98)
    make_alert(org, 3, severity="high", status="investigating", risk_score=90)
    make_alert(org, 4, severity="high", status="escalated", risk_score=85)
    aged(make_alert(org, 5, severity="critical", status="new", risk_score=95), 40)
    make_alert(Organisation.objects.create(name="Lekki Coop"), 1, severity="critical")

    body = client_for(uploader).get(DASH + "summary/").json()

    assert body["period_days"] == 30
    assert body["critical_alerts"] == 1
    assert body["open_investigations"] == 2
    assert body["active_cases"] == 0
    # Open alerts: critical 98 (w4), high 90 (w3), high 85 (w3) -> (392 + 270 + 255) / 10 = 91.7
    assert body["risk_exposure"] == {"score": 92, "level": "critical", "source": "derived"}
    assert body["alerts_by_severity"] == {"critical": 2, "high": 2, "medium": 0, "low": 0}
    assert body["alerts_by_status"] == {"new": 1, "investigating": 1, "escalated": 1, "false_positive": 1}


@pytest.mark.parametrize("days, expected", [(10, 1), (15, 2), (30, 3)])
def test_summary_period(uploader, org, days, expected):
    make_alert(org, 1, severity="critical")
    aged(make_alert(org, 2, severity="critical"), 12)
    aged(make_alert(org, 3, severity="critical"), 29)

    assert client_for(uploader).get(DASH + "summary/", {"days": days}).json()["critical_alerts"] == expected


@pytest.mark.parametrize("days", ["7", "31", "abc"])
def test_summary_rejects_other_periods(uploader, days):
    response = client_for(uploader).get(DASH + "summary/", {"days": days})

    assert response.status_code == 400 and "days" in response.json()


def test_summary_for_an_empty_organisation(uploader):
    body = client_for(uploader).get(DASH + "summary/").json()

    assert body["critical_alerts"] == body["open_investigations"] == body["active_cases"] == 0
    assert body["risk_exposure"] == {"score": 0, "level": "low", "source": "derived"}
    assert set(body["alerts_by_status"].values()) == {0}


@pytest.mark.parametrize("score, level", [(0, "low"), (24, "low"), (25, "medium"), (49, "medium"), (50, "high"), (74, "high"), (75, "critical"), (100, "critical")])
def test_exposure_level_bands(score, level):
    assert exposure_level(score) == level


def test_heatmap_counts_by_department_and_risk_type(uploader, org):
    make_alert(org, 1, department="Finance", rule=Alert.DUPLICATE_BANK_ACCOUNT, risk_type="Duplicate Bank Account")
    make_alert(org, 2, department="Finance", rule=Alert.DUPLICATE_BANK_ACCOUNT, risk_type="Duplicate Bank Account")
    make_alert(org, 3, department="Procurement")
    make_alert(org, 4, department="")
    aged(make_alert(org, 5, department="Procurement"), 10)

    weekly = client_for(uploader).get(DASH + "heatmap/", {"period": "weekly"}).json()
    monthly = client_for(uploader).get(DASH + "heatmap/").json()

    assert weekly["period"] == "weekly"
    assert weekly["departments"] == ["Finance", "Procurement", "Unassigned"]
    assert weekly["risk_types"] == ["Duplicate Bank Account", "Duplicate Payment"]
    assert weekly["cells"] == [
        {"department": "Finance", "risk_type": "Duplicate Bank Account", "count": 2},
        {"department": "Procurement", "risk_type": "Duplicate Payment", "count": 1},
        {"department": "Unassigned", "risk_type": "Duplicate Payment", "count": 1},
    ]
    assert monthly["period"] == "monthly"
    assert {"department": "Procurement", "risk_type": "Duplicate Payment", "count": 2} in monthly["cells"]


def test_heatmap_rejects_other_periods(uploader):
    assert client_for(uploader).get(DASH + "heatmap/", {"period": "yearly"}).status_code == 400


def test_trends_cover_30_days_with_zeros(uploader, org):
    make_alert(org, 1, severity="critical")
    make_alert(org, 2, severity="high")
    aged(make_alert(org, 3, severity="high"), 3)
    aged(make_alert(org, 4, severity="low"), 31)

    days = client_for(uploader).get(DASH + "trends/").json()["days"]

    assert len(days) == 30
    assert days[-1]["date"] == timezone.now().date().isoformat()
    assert days[-1] == {"date": days[-1]["date"], "critical": 1, "high": 1, "medium": 0, "low": 0}
    assert days[-4]["high"] == 1
    assert sum(d["low"] for d in days) == 0
    assert days[0] == {"date": days[0]["date"], "critical": 0, "high": 0, "medium": 0, "low": 0}


def test_investigation_queue_is_most_severe_first(uploader, org):
    make_alert(org, 1, severity="high", status="investigating")
    make_alert(org, 2, severity="critical", status="escalated")
    make_alert(org, 3, severity="critical", status="new")
    make_alert(org, 4, severity="high", status="investigating")

    body = client_for(uploader).get(DASH + "investigation-queue/").json()

    assert body["count"] == 3
    assert [a["reference"] for a in body["results"]] == ["ALT-0002", "ALT-0004", "ALT-0001"]


@pytest.mark.parametrize("path", ["summary/", "heatmap/", "trends/", "investigation-queue/"])
def test_dashboard_needs_a_token_and_a_changed_password(org, member, path):
    assert APIClient().get(DASH + path).status_code == 401
    User.objects.filter(pk=member.pk).update(must_change_password=True)
    member.refresh_from_db()

    assert client_for(member).get(DASH + path).status_code == 403


# ---- payment outside the pay period (client rule, 2026-10-22) ----

from fraud.services.detection import payments_outside_pay_period


def period_upload(organisation, start, end):
    return DataUpload.objects.create(
        organisation=organisation, kind=DataUpload.PAYROLL, file_name="p.csv", row_count=0, period_start=start, period_end=end
    )


def paid(upload, employee_id, day, name=None, account=None):
    return Payroll.objects.create(
        organisation=upload.organisation, upload=upload, employee_id=employee_id, employee_name=name or f"Employee {employee_id}",
        department="Operations", net_salary=Decimal("230000"), account_number=account or f"01{employee_id[-4:]:0>8}",
        pay_period=f"{upload.period_start} to {upload.period_end}", payment_date=day, approver_id="APR-1",
    )


def test_payments_inside_the_period_are_not_flagged(org):
    upload = period_upload(org, date(2026, 10, 2), date(2026, 10, 7))
    paid(upload, "E001", date(2026, 10, 2))
    paid(upload, "E002", date(2026, 10, 7))

    assert payments_outside_pay_period(org, upload) == []


def test_late_and_early_payments_are_flagged_by_severity(org):
    upload = period_upload(org, date(2026, 10, 2), date(2026, 10, 7))
    paid(upload, "E001", date(2026, 10, 5))                  # inside
    paid(upload, "E002", date(2026, 10, 10), name="Bola")    # 3 days late -> medium
    paid(upload, "E003", date(2026, 10, 20), name="Chi")     # 13 days late -> high
    paid(upload, "E004", date(2026, 10, 1), name="Dayo")     # 1 day early -> high

    findings = {f["entity_ref"]: f for f in payments_outside_pay_period(org, upload)}

    assert set(findings) == {"E002", "E003", "E004"}
    assert (findings["E002"]["severity"], findings["E002"]["risk_score"]) == ("medium", 55)
    assert findings["E003"]["severity"] == "high" and findings["E004"]["severity"] == "high"
    medium = findings["E002"]
    assert medium["rule"] == Alert.PAYMENT_OUTSIDE_PAY_PERIOD and medium["risk_type"] == "Payment Outside Pay Period"
    assert medium["title"] == "Bola paid outside the 2026-10-02 to 2026-10-07 pay period"
    assert medium["flagged_records"][0]["outside_by"] == "3 days after"
    assert findings["E004"]["flagged_records"][0]["outside_by"] == "1 day before"
    assert medium["fingerprint"] == "payment_outside_pay_period:2026-10-02 to 2026-10-07:E002"


def test_one_alert_per_employee_lists_every_outside_payment(org):
    upload = period_upload(org, date(2026, 10, 2), date(2026, 10, 7))
    paid(upload, "E009", date(2026, 10, 9), account="0100000091")
    paid(upload, "E009", date(2026, 10, 12), account="0100000092")

    [finding] = payments_outside_pay_period(org, upload)

    assert [r["payment_date"] for r in finding["flagged_records"]] == ["2026-10-09", "2026-10-12"]


def test_uploads_without_a_period_are_skipped(org):
    upload = make_upload(org, DataUpload.PAYROLL)

    assert payments_outside_pay_period(org, upload) == []


def test_the_upload_raises_the_alert_end_to_end(uploader):
    file = csv("p.csv", PAYROLL_HEADER, ["E1,Ada,Ops,100,90,0123456789,2026-09,2026-10-15,Clerk,APR-01,22,Active"])

    body = client_for(uploader).post(
        PAYROLL_URL, {"pay_period_start": "2026-10-02", "pay_period_end": "2026-10-07", "file": file}, format="multipart"
    ).json()

    assert body["detection"]["alerts_created"] == 1
    alert = Alert.objects.get(rule=Alert.PAYMENT_OUTSIDE_PAY_PERIOD)
    assert alert.severity == "high" and alert.entity_ref == "E1"
