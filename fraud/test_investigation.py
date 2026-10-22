from datetime import date
from decimal import Decimal

import pytest

from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from accounts.models import Organisation
from fraud.models import Alert, Case
from fraud.services.investigation import InvestigationService
from fraud.test_cases import client_for, make_alert
from ingestion.models import DataUpload, Payment, Payroll, Vendor

User = get_user_model()
ALERTS = "/api/v1/fraud/alerts/"
CASES = "/api/v1/fraud/cases/"
SHAPE = {"subject", "alerts", "related", "linked_alerts", "transactions", "analysis", "pattern_detections", "timeline"}


@pytest.fixture
def org(db):
    return Organisation.objects.create(name="Ikeja Coop")


@pytest.fixture
def other_org(db):
    return Organisation.objects.create(name="Lekki Coop")


def upload(organisation, kind):
    return DataUpload.objects.create(organisation=organisation, kind=kind, file_name=f"{kind}.xlsx", row_count=1)


def salary(organisation, employee_id, name, account, paid, amount="150000", **extra):
    return Payroll.objects.create(
        organisation=organisation, upload=upload(organisation, DataUpload.PAYROLL), employee_id=employee_id,
        employee_name=name, department=extra.pop("department", "Operations"), net_salary=Decimal(amount),
        account_number=account, pay_period="2026-05-01 to 2026-05-07", payment_date=paid, **extra,
    )


def payment(organisation, transaction_id, vendor_id, paid, amount="42350"):
    return Payment.objects.create(
        organisation=organisation, upload=upload(organisation, DataUpload.PAYMENTS), transaction_id=transaction_id,
        vendor_id=vendor_id, invoice_number="INV-1", amount=Decimal(amount), payment_date=paid,
    )


def bank_alert(organisation, number, **overrides):
    values = {
        "rule": Alert.DUPLICATE_BANK_ACCOUNT, "risk_type": "Duplicate Bank Account", "severity": "critical",
        "risk_score": 95, "entity_type": "employee", "entity_name": "Ada Bello", "entity_ref": "EMP001",
        "department": "Operations",
        "flagged_records": [
            {"employee_id": "EMP001", "employee_name": "Ada Bello", "department": "Operations", "account_number": "0123456789",
             "net_salary": "150,000.00", "payment_date": "2026-05-03", "approver_id": "EMP900"},
            {"employee_id": "EMP002", "employee_name": "Bola Ade", "department": "Finance", "account_number": "0123456789",
             "net_salary": "120,000.00", "payment_date": "2026-05-03", "approver_id": "EMP900"},
        ],
        "timeline": [
            {"date": "2026-05-03", "label": "Salary paid", "detail": "Ada Bello: 150,000.00 to 0123456789"},
            {"date": "2026-05-03", "label": "Salary paid", "detail": "Bola Ade: 120,000.00 to 0123456789"},
        ],
    }
    values.update(overrides)
    return make_alert(organisation, number, **values)


def build(organisation, *alerts):
    return InvestigationService.build(organisation, list(alerts), {"type": "alert", "reference": alerts[0].reference})


def test_duplicate_payment_analysis_and_connected_payments(org, other_org):
    alert = make_alert(org, 2, timeline=[
        {"date": "2026-05-08", "label": "Payment", "detail": "TXN-22: paid 42,350.00"},
        {"date": "2026-05-05", "label": "Payment", "detail": "TXN-21: paid 42,350.00"},
    ])
    Vendor.objects.create(
        organisation=org, upload=upload(org, DataUpload.VENDORS), vendor_id="VND-8847", vendor_name="Acme Supplies Ltd",
        account_number="9988776655", bank_name="GTBank", registration_number="RC-1",
    )
    payment(org, "TXN-21", "VND-8847", date(2026, 5, 5))
    payment(org, "TXN-22", "VND-8847", date(2026, 5, 8))
    payment(org, "TXN-30", "VND-8847", date(2026, 6, 1), amount="10000")
    payment(other_org, "TXN-99", "VND-8847", date(2026, 6, 2))

    result = build(org, alert)

    assert result["related"]["vendors"] == [
        {"id": "VND-8847", "name": "Acme Supplies Ltd", "bank_name": "GTBank", "registration_number": "RC-1"}
    ]
    assert result["related"]["approvers"] == [{"id": "", "name": "John Doe", "role": "", "department": ""}]
    assert result["analysis"] == {
        "total_exposure": "84,700.00", "duplicate_amount": "42,350.00", "risk_score": 90,
        "transaction_count": 2, "currency": "NGN", "source": "derived",
    }
    transactions = result["transactions"]
    assert transactions["count"] == 3
    assert [(t["id"], t["status"], t["alerts"]) for t in transactions["results"]] == [
        ("TXN-30", "clear", []), ("TXN-22", "flagged", ["ALT-0002"]), ("TXN-21", "flagged", ["ALT-0002"]),
    ]
    assert transactions["results"][0] == {
        "kind": "payment", "id": "TXN-30", "description": "Acme Supplies Ltd, invoice INV-1", "amount": "10,000.00",
        "date": "2026-06-01", "status": "clear", "alerts": [],
    }
    assert [event["date"] for event in result["timeline"]] == ["2026-05-05", "2026-05-08"]
    assert result["timeline"][0]["alert"] == "ALT-0002"


def test_shared_account_related_people_and_salaries(org):
    alert = bank_alert(org, 3)
    salary(org, "EMP001", "Ada Bello", "0123456789", date(2026, 5, 3), role="Clerk")
    salary(org, "EMP002", "Bola Ade", "0123456789", date(2026, 5, 3), amount="120000", department="Finance")
    salary(org, "EMP003", "Chidi Eze", "0123456789", date(2026, 4, 3))
    salary(org, "EMP001", "Ada Bello", "0123456789", date(2026, 4, 3), role="Clerk")
    salary(org, "EMP900", "John Doe", "5555555555", date(2026, 5, 3), role="Manager", department="Finance")
    salary(org, "EMP777", "Unrelated", "1111111111", date(2026, 5, 3))

    result = build(org, alert)

    assert result["related"]["employees"] == [
        {"id": "EMP001", "name": "Ada Bello", "role": "Clerk", "department": "Operations"},
        {"id": "EMP002", "name": "Bola Ade", "role": "", "department": "Finance"},
    ]
    assert result["related"]["approvers"] == [{"id": "EMP900", "name": "John Doe", "role": "Manager", "department": "Finance"}]
    assert result["analysis"]["total_exposure"] == "270,000.00"
    assert result["analysis"]["duplicate_amount"] == "120,000.00"
    assert result["analysis"]["transaction_count"] == 2
    statuses = {(t["id"], t["date"]): t["status"] for t in result["transactions"]["results"]}
    assert statuses == {
        ("EMP001", "2026-05-03"): "flagged", ("EMP002", "2026-05-03"): "flagged",
        ("EMP003", "2026-04-03"): "clear", ("EMP001", "2026-04-03"): "clear",
    }
    assert result["transactions"]["results"][0]["description"] == "Salary: Ada Bello (2026-05-01 to 2026-05-07)"


def test_unknown_people_fall_back_to_the_alert(org):
    result = build(org, bank_alert(org, 3))

    assert result["related"]["employees"][1] == {"id": "EMP002", "name": "Bola Ade", "role": "", "department": "Finance"}
    assert result["related"]["approvers"] == [{"id": "EMP900", "name": "", "role": "", "department": ""}]
    assert result["transactions"] == {"count": 0, "results": []}


def test_linked_alerts_are_other_alerts_about_the_same_people(org, other_org):
    alert = bank_alert(org, 3)
    same_employee = bank_alert(org, 4, entity_ref="EMP002", entity_name="Bola Ade", fingerprint="other:4", flagged_records=[
        {"employee_id": "EMP002", "payment_date": "2026-04-03", "net_salary": "1.00"},
    ])
    bank_alert(org, 5, entity_ref="EMP777", fingerprint="other:5")
    make_alert(org, 6, entity_ref="EMP001")  # A vendor that happens to share the ID is not the employee.
    bank_alert(other_org, 3)
    salary(org, "EMP002", "Bola Ade", "0123456789", date(2026, 4, 3))

    result = build(org, alert)

    assert result["linked_alerts"] == [same_employee]
    april = next(t for t in result["transactions"]["results"] if t["date"] == "2026-04-03")
    assert (april["status"], april["alerts"]) == ("flagged", ["ALT-0004"])


def test_a_case_sums_its_alerts_and_excludes_them_from_linked(org):
    first, second = make_alert(org, 2), bank_alert(org, 3)

    result = InvestigationService.build(org, [first, second], {"type": "case", "reference": "CASE-0001"})

    assert result["linked_alerts"] == []
    assert result["analysis"]["total_exposure"] == "354,700.00"
    assert result["analysis"]["duplicate_amount"] == "162,350.00"
    assert result["analysis"]["risk_score"] == 95
    assert result["analysis"]["transaction_count"] == 4


def member_of(organisation, username):
    return User.objects.create_user(
        username=username, email=f"{username}@example.ng", password="Str0ng-Passw0rd!", full_name=username.title(),
        organisation=organisation, account_type="super_admin", is_verified=True,
    )


def test_alert_investigation_endpoint(org, other_org):
    alert = make_alert(org, 2)
    linked = make_alert(org, 7, fingerprint="test:linked")
    linked.case = Case.objects.create(organisation=org, number=3, title="Acme", priority="high")
    linked.save()
    payment(org, "TXN-21", "VND-8847", date(2026, 5, 5))

    response = client_for(member_of(org, "ngozi")).get(f"{ALERTS}alt-0002/investigation/")

    assert response.status_code == 200
    body = response.json()
    assert set(body) == SHAPE
    assert body["subject"] == {"type": "alert", "reference": "ALT-0002"}
    assert [a["reference"] for a in body["alerts"]] == [alert.reference]
    assert [(a["reference"], a["case"]) for a in body["linked_alerts"]] == [("ALT-0007", "CASE-0003")]
    assert body["transactions"]["results"][0]["alerts"] == ["ALT-0002"]
    assert body["analysis"]["total_exposure"] == "84,700.00"


def test_alert_investigation_errors(org, other_org):
    make_alert(org, 2)
    url = f"{ALERTS}ALT-0002/investigation/"

    assert APIClient().get(url).status_code == 401
    assert client_for(member_of(other_org, "lekki")).get(url).status_code == 404
    assert client_for(member_of(org, "ngozi")).get(f"{ALERTS}nope/investigation/").status_code == 404


def test_case_investigation_endpoint(org):
    case = Case.objects.create(organisation=org, number=1, title="Acme and shared account", priority="critical")
    first, second = make_alert(org, 2, case=case), bank_alert(org, 3, case=case)
    outside = bank_alert(org, 4, entity_ref="EMP002", fingerprint="other:4")

    response = client_for(member_of(org, "ngozi")).get(f"{CASES}case-0001/investigation/")

    assert response.status_code == 200
    body = response.json()
    assert body["subject"] == {"type": "case", "reference": "CASE-0001"}
    assert [a["reference"] for a in body["alerts"]] == [first.reference, second.reference]
    assert [a["reference"] for a in body["linked_alerts"]] == [outside.reference]
    assert body["analysis"]["total_exposure"] == "354,700.00"
    assert body["analysis"]["risk_score"] == 95
    assert {event["alert"] for event in body["timeline"]} == {"ALT-0003"}


def test_case_investigation_errors(org, other_org):
    Case.objects.create(organisation=org, number=1, title="Acme", priority="high")
    url = f"{CASES}CASE-0001/investigation/"

    assert APIClient().get(url).status_code == 401
    assert client_for(member_of(other_org, "lekki")).get(url).status_code == 404
    empty = client_for(member_of(org, "ngozi")).get(url)
    assert empty.status_code == 200
    assert empty.json()["analysis"] == {
        "total_exposure": "0.00", "duplicate_amount": "0.00", "risk_score": 0, "transaction_count": 0,
        "currency": "NGN", "source": "derived",
    }
