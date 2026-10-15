from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from accounts.models import AuditLog, Organisation
from fraud.models import Alert, AlertEvent, Case, CaseComment, CaseEvent

User = get_user_model()
CASES = "/api/v1/fraud/cases/"


@pytest.fixture
def org(db):
    return Organisation.objects.create(name="Ikeja Coop")


@pytest.fixture
def owner(org):
    return User.objects.create_user(
        username="owner", email="owner@ikejacoop.ng", password="Str0ng-Passw0rd!", full_name="Ngozi Okafor",
        organisation=org, account_type="super_admin", is_verified=True,
    )


@pytest.fixture
def member(org):
    return User.objects.create_user(
        username="ada", email="ada@ikejacoop.ng", password="Str0ng-Passw0rd!", full_name="Ada Obi", organisation=org,
        account_type="team_member", is_verified=True,
    )


@pytest.fixture
def outsider(db):
    other = Organisation.objects.create(name="Lekki Coop")
    return User.objects.create_user(
        username="boss", email="boss@lekkicoop.ng", password="Str0ng-Passw0rd!", full_name="Lekki Boss",
        organisation=other, account_type="super_admin", is_verified=True,
    )


def client_for(user):
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION="Bearer " + str(AccessToken.for_user(user)))
    return client


def make_alert(organisation, number, **overrides):
    values = {
        "rule": Alert.DUPLICATE_PAYMENT, "risk_type": "Duplicate Payment", "title": f"Alert {number}",
        "explanation": "Paid twice.", "severity": "high", "risk_score": 90, "confidence": 90, "entity_type": "vendor",
        "entity_name": "Acme Supplies", "entity_ref": "VND-8847", "department": "Procurement",
        "fingerprint": f"test:{number}",
        "flagged_records": [
            {"transaction_id": f"TXN-{number}1", "payment_date": "2026-05-05", "amount": "42,350.00", "invoice_number": "INV-1", "approver": "John Doe"},
            {"transaction_id": f"TXN-{number}2", "payment_date": "2026-05-08", "amount": "42,350.00", "invoice_number": "INV-1", "approver": "John Doe"},
        ],
    }
    values.update(overrides)
    return Alert.objects.create(organisation=organisation, number=number, **values)


def payroll_alert(organisation, number):
    return make_alert(
        organisation, number, rule=Alert.DUPLICATE_BANK_ACCOUNT, risk_type="Duplicate Bank Account", severity="critical",
        entity_type="employee", entity_name="Ada Bello", entity_ref="EMP001", department="Operations",
        flagged_records=[
            {"employee_id": "EMP001", "employee_name": "Ada Bello", "department": "Operations", "account_number": "0123456789"},
            {"employee_id": "EMP002", "employee_name": "Bola Ade", "department": "Finance", "account_number": "0123456789"},
        ],
    )


def open_case(user, *references, **extra):
    return client_for(user).post(CASES, {"alerts": list(references), **extra}, format="json")


# ---- 16a: convert alerts to a case ----


def test_convert_one_alert(owner, member, org):
    alert = make_alert(org, 2)

    response = open_case(owner, "ALT-0002", investigator_id=str(member.id))

    assert response.status_code == 201
    body = response.json()
    assert body["reference"] == "CASE-0001" and body["status"] == "new" and body["priority"] == "high"
    assert body["title"] == "Alert 2" and body["alerts_count"] == 1
    assert body["investigator"]["full_name"] == "Ada Obi" and body["created_by"]["full_name"] == "Ngozi Okafor"
    assert [a["reference"] for a in body["alerts"]] == ["ALT-0002"]
    assert body["related"] == {"employees": [], "vendors": [{"id": "VND-8847", "name": "Acme Supplies"}], "approvers": ["John Doe"]}
    assert [t["transaction_id"] for t in body["transactions"]] == ["TXN-21", "TXN-22"] and body["transactions"][0]["alert"] == "ALT-0002"
    assert body["sla_days_remaining"] == 5 and body["days_open"] == 0 and body["escalation"] is None
    alert.refresh_from_db()
    assert alert.status == "investigating" and alert.case.reference == "CASE-0001"
    assert alert.events.get(type=AlertEvent.STATUS_CHANGED).detail == "Converted to CASE-0001"
    case = Case.objects.get()
    assert list(case.events.values_list("type", flat=True)) == ["created", "investigator_assigned"]
    assert AuditLog.objects.filter(action=AuditLog.CASE_CREATED, details__reference="CASE-0001").exists()
    assert AuditLog.objects.filter(action=AuditLog.ALERT_STATUS_CHANGED, details__case="CASE-0001").exists()
    assert client_for(owner).get("/api/v1/fraud/alerts/ALT-0002/").json()["case"] == "CASE-0001"


def test_convert_several_alerts(owner, org):
    make_alert(org, 1, severity="medium")
    payroll_alert(org, 2)
    make_alert(org, 3, status="escalated")

    body = open_case(owner, "alt-0001", "ALT-0002", "ALT-0002", "ALT-0003").json()

    assert body["alerts_count"] == 3 and body["priority"] == "critical"
    assert body["title"] == "Alert 1 (+2 more alerts)"
    assert body["related"]["employees"] == [
        {"id": "EMP001", "name": "Ada Bello", "department": "Operations"},
        {"id": "EMP002", "name": "Bola Ade", "department": "Finance"},
    ]
    assert Alert.objects.get(number=3).status == "escalated"  # Only `new` alerts move.
    assert not AlertEvent.objects.filter(alert__number=3).exists()


def test_case_numbers_count_per_organisation(owner, outsider, org):
    make_alert(org, 1)
    make_alert(org, 2)
    make_alert(outsider.organisation, 1)

    assert open_case(owner, "ALT-0001").json()["reference"] == "CASE-0001"
    assert open_case(owner, "ALT-0002", title="Own title").json()["reference"] == "CASE-0002"
    assert open_case(outsider, "ALT-0001").json()["reference"] == "CASE-0001"
    assert Case.objects.get(organisation=org, number=2).title == "Own title"


@pytest.mark.parametrize(
    "references, message",
    [
        (["ALT-0099"], "No alert ALT-0099 in your organisation."),
        (["CASE-1"], "No alert CASE-1 in your organisation."),
        (["ALT-0003"], "ALT-0003 is marked false positive; change its status first."),
    ],
)
def test_convert_refuses_bad_alerts(owner, org, references, message):
    make_alert(org, 1)
    make_alert(org, 3, status="false_positive")

    response = open_case(owner, "ALT-0001", *references)

    assert response.status_code == 400 and response.json() == {"alerts": [message]}
    assert not Case.objects.exists() and Alert.objects.get(number=1).status == "new"


def test_an_alert_joins_only_one_case(owner, org):
    make_alert(org, 2)
    open_case(owner, "ALT-0002")

    response = open_case(owner, "ALT-0002")

    assert response.status_code == 400 and response.json() == {"alerts": ["ALT-0002 is already in CASE-0001."]}


def test_convert_refuses_other_organisations_alerts_and_people(owner, outsider, org):
    make_alert(outsider.organisation, 1)
    make_alert(org, 2)

    assert open_case(owner, "ALT-0001").json() == {"alerts": ["No alert ALT-0001 in your organisation."]}
    response = open_case(owner, "ALT-0002", investigator_id=str(outsider.id))
    assert response.status_code == 400 and "investigator_id" in response.json()


@pytest.mark.parametrize("body", [{}, {"alerts": []}, {"alerts": [f"ALT-{n:04d}" for n in range(21)]}, {"alerts": ["ALT-0001"], "title": "x" * 256}])
def test_convert_validates_the_body(owner, org, body):
    make_alert(org, 1)

    assert client_for(owner).post(CASES, body, format="json").status_code == 400


# ---- 16a: list and detail ----


def test_list_filters_and_search(owner, member, org, outsider):
    make_alert(org, 1, entity_name="Phantom Solutions LLC")
    make_alert(org, 2)
    make_alert(org, 3, severity="critical")
    make_alert(outsider.organisation, 1)
    open_case(owner, "ALT-0001")
    open_case(owner, "ALT-0002", investigator_id=str(member.id))
    open_case(owner, "ALT-0003")
    open_case(outsider, "ALT-0001")
    Case.objects.filter(organisation=org, number=3).update(status="closed")
    client = client_for(owner)

    def refs(**params):
        return [c["reference"] for c in client.get(CASES, params).json()["results"]]

    assert refs() == ["CASE-0003", "CASE-0002", "CASE-0001"]
    assert refs(status="closed") == ["CASE-0003"]
    assert refs(priority="critical") == ["CASE-0003"]
    assert refs(investigator=str(member.id)) == ["CASE-0002"]
    assert refs(investigator="none") == ["CASE-0003", "CASE-0001"]
    assert refs(search="case-0002") == ["CASE-0002"]
    assert refs(search="ALT-0003") == ["CASE-0003"]
    assert refs(search="phantom") == ["CASE-0001"]
    assert refs(search="Alert 2") == ["CASE-0002"]
    row = client.get(CASES).json()["results"][0]
    assert set(row) == {"reference", "title", "status", "priority", "investigator", "alerts_count", "sla_due_at", "sla_days_remaining", "days_open", "created_at", "updated_at"}
    assert row["sla_days_remaining"] is None  # Closed.


@pytest.mark.parametrize("params", [{"status": "open"}, {"priority": "urgent"}, {"investigator": "ada"}])
def test_list_rejects_bad_filters(owner, params):
    assert client_for(owner).get(CASES, params).status_code == 400


def test_list_is_paginated(owner, org):
    for n in range(1, 4):
        make_alert(org, n)
        open_case(owner, f"ALT-{n:04d}")

    body = client_for(owner).get(CASES, {"page_size": 2}).json()

    assert body["count"] == 3 and len(body["results"]) == 2 and body["next"]


def test_other_organisations_cases_are_404(owner, outsider, org):
    make_alert(org, 1)
    open_case(owner, "ALT-0001")
    client = client_for(outsider)

    assert client.get(CASES + "CASE-0001/").status_code == 404
    assert client.post(CASES + "CASE-0001/status/", {"status": "closed"}, format="json").status_code == 404
    assert client.get(CASES + "nonsense/").status_code == 404
    assert client.get(CASES).json()["count"] == 0


def test_cases_need_a_token_and_a_changed_password(member):
    assert APIClient().get(CASES).status_code == 401
    User.objects.filter(pk=member.pk).update(must_change_password=True)
    member.refresh_from_db()

    assert client_for(member).get(CASES).status_code == 403


def test_sla_follows_priority_and_age(owner, org):
    make_alert(org, 1, severity="low")
    open_case(owner, "ALT-0001")
    Case.objects.update(created_at=timezone.now() - timedelta(days=15, hours=1))

    body = client_for(owner).get(CASES + "case-0001/").json()

    assert body["priority"] == "low" and body["days_open"] == 15 and body["sla_days_remaining"] == -1


# ---- 16b: status, assignment, history ----


def test_status_changes_close_and_reopen(owner, org):
    make_alert(org, 1)
    open_case(owner, "ALT-0001")
    client = client_for(owner)
    url = CASES + "CASE-0001/status/"

    assert client.post(url, {"status": "investigating"}, format="json").json()["status"] == "investigating"
    assert client.post(url, {"status": "under_review"}, format="json").status_code == 200
    closed = client.post(url, {"status": "closed", "note": "Refunded."}, format="json").json()
    assert closed["closed_at"] and closed["sla_days_remaining"] is None
    reopened = client.post(url, {"status": "investigating"}, format="json").json()
    assert reopened["closed_at"] is None and reopened["sla_days_remaining"] == 5

    changes = list(CaseEvent.objects.filter(type="status_changed").values_list("from_status", "to_status", "detail"))
    assert changes == [("new", "investigating", ""), ("investigating", "under_review", ""), ("under_review", "closed", "Refunded."), ("closed", "investigating", "")]
    assert AuditLog.objects.filter(action=AuditLog.CASE_STATUS_CHANGED).count() == 4


@pytest.mark.parametrize(
    "body, error",
    [
        ({"status": "new"}, {"status": ["The case is already New."]}),
        ({"status": "escalated"}, {"status": ["Use the escalate endpoint to escalate a case."]}),
        ({"status": "open"}, {"status": ['"open" is not a valid choice.']}),
        ({"status": "closed", "note": "x" * 2001}, {"note": ["Ensure this field has no more than 2000 characters."]}),
    ],
)
def test_status_refusals(owner, org, body, error):
    make_alert(org, 1)
    open_case(owner, "ALT-0001")

    response = client_for(owner).post(CASES + "CASE-0001/status/", body, format="json")

    assert response.status_code == 400 and response.json() == error


def test_assign_and_remove_people(owner, member, org):
    make_alert(org, 1)
    open_case(owner, "ALT-0001")
    client = client_for(owner)
    url = CASES + "CASE-0001/assignment/"

    both = client.put(url, {"investigator_id": str(member.id), "reviewer_id": str(owner.id)}, format="json").json()
    assert both["investigator"]["full_name"] == "Ada Obi" and both["reviewer"]["full_name"] == "Ngozi Okafor"
    same = client.put(url, {"investigator_id": str(member.id)}, format="json")
    assert same.status_code == 200
    removed = client.put(url, {"reviewer_id": None}, format="json").json()
    assert removed["reviewer"] is None and removed["investigator"]["full_name"] == "Ada Obi"

    events = list(CaseEvent.objects.exclude(type="created").values_list("type", "detail"))
    assert events == [("investigator_assigned", "Ada Obi"), ("reviewer_assigned", "Ngozi Okafor"), ("reviewer_removed", "")]
    assert AuditLog.objects.filter(action=AuditLog.CASE_ASSIGNED).count() == 2  # Nothing changed the second time.


def test_assignment_refuses_non_members(owner, member, outsider, org):
    make_alert(org, 1)
    open_case(owner, "ALT-0001")
    User.objects.filter(pk=member.pk).update(is_active=False)
    client = client_for(owner)
    url = CASES + "CASE-0001/assignment/"

    assert client.put(url, {"investigator_id": str(outsider.id)}, format="json").json() == {"investigator_id": ["Choose an active member of your organisation."]}
    assert client.put(url, {"reviewer_id": str(member.id)}, format="json").status_code == 400
    assert client.put(url, {"investigator_id": str(owner.id), "reviewer_id": "not-a-uuid"}, format="json").status_code == 400
    assert client.put(url, {}, format="json").json() == {"non_field_errors": ["Send `investigator_id`, `reviewer_id`, or both."]}
    assert Case.objects.get().investigator is None


def test_history_is_oldest_first(owner, member, org):
    make_alert(org, 1)
    open_case(owner, "ALT-0001")
    client = client_for(owner)
    client.post(CASES + "CASE-0001/status/", {"status": "investigating"}, format="json")
    client.post(CASES + "CASE-0001/comments/", {"text": "Called the bank."}, format="json")
    # Windows clocks tick in ~15 ms steps, so spread the events out.
    for offset, event in enumerate(CaseEvent.objects.order_by("created_at", "type")):
        CaseEvent.objects.filter(pk=event.pk).update(created_at=timezone.now() + timedelta(seconds=offset))

    body = client.get(CASES + "CASE-0001/history/").json()

    assert body["count"] == 3
    assert [e["type"] for e in body["results"]] == ["created", "status_changed", "comment_added"]
    assert body["results"][0]["actor"]["full_name"] == "Ngozi Okafor"


# ---- 16c: comments ----


def test_comments(owner, member, org):
    make_alert(org, 1)
    open_case(owner, "ALT-0001")
    url = CASES + "CASE-0001/comments/"

    first = client_for(owner).post(url, {"text": "  Called the bank.  "}, format="json")
    client_for(member).post(url, {"text": "Statement received."}, format="json")
    CaseComment.objects.filter(text="Called the bank.").update(created_at=timezone.now() - timedelta(minutes=1))

    assert first.status_code == 201 and first.json()["text"] == "Called the bank."
    body = client_for(owner).get(url).json()
    assert [c["text"] for c in body["results"]] == ["Statement received.", "Called the bank."]
    assert body["results"][0]["author"]["full_name"] == "Ada Obi"
    assert client_for(owner).get(CASES + "CASE-0001/").json()["comments_count"] == 2
    assert AuditLog.objects.filter(action=AuditLog.CASE_COMMENT_ADDED).count() == 2


@pytest.mark.parametrize("text", ["", "   ", "x" * 2001])
def test_comment_text_rules(owner, org, text):
    make_alert(org, 1)
    open_case(owner, "ALT-0001")

    assert client_for(owner).post(CASES + "CASE-0001/comments/", {"text": text}, format="json").status_code == 400
    assert not CaseComment.objects.exists()


# ---- 16e: escalate ----


def test_escalate(owner, member, org):
    make_alert(org, 1)
    open_case(owner, "ALT-0001")

    response = client_for(owner).post(
        CASES + "CASE-0001/escalate/",
        {"reason": "financial_threshold", "escalate_to_id": str(member.id), "priority": "critical", "notes": "Over the limit."},
        format="json",
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "escalated" and body["priority"] == "critical" and body["sla_days_remaining"] == 2
    escalation = body["escalation"]
    assert escalation["reason"] == "financial_threshold" and escalation["reason_label"] == "Exceeds financial approval limit"
    assert escalation["escalated_to"]["full_name"] == "Ada Obi" and escalation["notes"] == "Over the limit." and escalation["escalated_at"]
    event = CaseEvent.objects.get(type="escalated")
    assert (event.from_status, event.to_status) == ("new", "escalated")
    assert event.detail == "Escalated to Ada Obi: Exceeds financial approval limit"
    assert AuditLog.objects.get(action=AuditLog.CASE_ESCALATED).details["priority"] == "critical"
    # Escalated cases leave that status through the status endpoint.
    assert client_for(owner).post(CASES + "CASE-0001/status/", {"status": "investigating"}, format="json").status_code == 200


@pytest.mark.parametrize(
    "body, field",
    [
        ({"reason": "budget", "priority": "high"}, "reason"),
        ({"reason": "legal_risk", "priority": "urgent"}, "priority"),
        ({"reason": "legal_risk"}, "priority"),
        ({"reason": "legal_risk", "priority": "high", "escalate_to_id": "outsider"}, "escalate_to_id"),
    ],
)
def test_escalate_refusals(owner, member, outsider, org, body, field):
    make_alert(org, 1)
    open_case(owner, "ALT-0001")
    body = {"escalate_to_id": str(member.id), **body}
    if body["escalate_to_id"] == "outsider":
        body["escalate_to_id"] = str(outsider.id)

    response = client_for(owner).post(CASES + "CASE-0001/escalate/", body, format="json")

    assert response.status_code == 400 and field in response.json()
    assert Case.objects.get().status == "new"


def test_closed_case_cannot_be_escalated(owner, member, org):
    make_alert(org, 1)
    open_case(owner, "ALT-0001")
    Case.objects.update(status="closed", closed_at=timezone.now())

    response = client_for(owner).post(
        CASES + "CASE-0001/escalate/", {"reason": "legal_risk", "escalate_to_id": str(member.id), "priority": "high"}, format="json"
    )

    assert response.status_code == 400 and response.json() == {"status": ["A closed case cannot be escalated. Reopen it first."]}


# ---- dashboard ----


def test_dashboard_counts_active_cases(owner, org, outsider):
    for n in range(1, 5):
        make_alert(org, n)
        open_case(owner, f"ALT-{n:04d}")
    Case.objects.filter(number=2).update(status="closed")
    Case.objects.filter(number=3).update(status="escalated")
    Case.objects.filter(number=4).update(created_at=timezone.now() - timedelta(days=12))
    make_alert(outsider.organisation, 1)
    open_case(outsider, "ALT-0001")
    client = client_for(owner)

    assert client.get("/api/v1/dashboard/summary/").json()["active_cases"] == 3
    assert client.get("/api/v1/dashboard/summary/", {"days": 10}).json()["active_cases"] == 2
