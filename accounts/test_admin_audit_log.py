import csv
import importlib
import io
import uuid
from datetime import timedelta

import pytest
from django.core.exceptions import PermissionDenied
from django.db import connection, models
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from accounts.models import AuditChain, AuditLog, Organisation, User, audit_hash
from accounts.services import AuditLogService

backfill = importlib.import_module("accounts.migrations.0024_audit_log_hash_chain").chain_existing_rows


@pytest.fixture
def org(db):
    return Organisation.objects.create(name="Ikeja Coop")


@pytest.fixture
def member(org):
    return User.objects.create_user(
        username="ngozi", email="ngozi@ikejacoop.ng", password="Str0ng-Passw0rd!", full_name="Ngozi Okafor",
        organisation=org, account_type="super_admin", is_verified=True,
    )


def write_rows(member, count=3):
    return [AuditLog.record(AuditLog.LOGIN, actor=member, details={"n": n}) for n in range(count)]


def tamper(row, **values):
    """Changes a row the way someone with database access could, around the model's refusal."""
    models.QuerySet.update(AuditLog.objects.filter(pk=row.pk), **values)


def remove(row):
    queryset = AuditLog.objects.filter(pk=row.pk)
    models.QuerySet._raw_delete(queryset, queryset.db)


def test_rows_join_the_chain_in_order(member):
    first, second, third = write_rows(member)

    assert [first.sequence, second.sequence, third.sequence] == [1, 2, 3]
    assert first.previous_hash == ""
    assert second.previous_hash == first.hash and third.previous_hash == second.hash
    assert len({first.hash, second.hash, third.hash}) == 3
    head = AuditChain.objects.get(pk=1)
    assert (head.last_sequence, head.last_hash) == (3, third.hash)


def test_plain_create_also_chains(member):
    row = AuditLog.objects.create(action=AuditLog.LOGOUT, actor=member, actor_email=member.email)

    assert row.sequence == 1 and len(row.hash) == 64


def test_bulk_create_is_refused(member):
    with pytest.raises(PermissionDenied):
        AuditLog.objects.bulk_create([AuditLog(action=AuditLog.LOGIN)])


def test_intact_log_verifies(member):
    rows = write_rows(member)

    result = AuditLogService.verify()

    assert result["valid"] is True
    assert (result["checked"], result["last_sequence"], result["last_hash"], result["problem"]) == (3, 3, rows[-1].hash, None)


def test_empty_log_verifies(db):
    result = AuditLogService.verify()

    assert (result["valid"], result["checked"], result["last_sequence"]) == (True, 0, 0)


@pytest.mark.parametrize("change", [{"actor_email": "someone@else.ng"}, {"details": {"n": 99}}, {"action": AuditLog.LOGOUT}, {"ip_address": "9.9.9.9"}])
def test_a_changed_row_is_reported(member, change):
    rows = write_rows(member)
    tamper(rows[1], **change)

    result = AuditLogService.verify()

    assert result["valid"] is False
    assert result["checked"] == 1
    assert {k: result["problem"][k] for k in ("sequence", "id", "reason")} == {"sequence": 2, "id": str(rows[1].id), "reason": "altered"}


def test_a_rehashed_row_breaks_the_next_link(member):
    rows = write_rows(member)
    tamper(rows[1], actor_email="someone@else.ng")
    rows[1].refresh_from_db()
    tamper(rows[1], hash=audit_hash(rows[1]))

    problem = AuditLogService.verify()["problem"]

    assert (problem["sequence"], problem["reason"]) == (3, "broken_link")


def test_a_deleted_row_in_the_middle_is_reported(member):
    rows = write_rows(member)
    remove(rows[1])

    result = AuditLogService.verify()

    assert result["valid"] is False
    assert (result["problem"]["sequence"], result["problem"]["reason"]) == (3, "missing")
    assert result["problem"]["detail"] == "Row 3: Rows 2 to 2 are missing."


def test_deleted_newest_rows_are_reported(member):
    rows = write_rows(member)
    remove(rows[2])

    result = AuditLogService.verify()

    assert result["valid"] is False
    assert result["problem"] == {"sequence": 3, "id": None, "reason": "missing", "detail": "Rows 3 to 3 are missing from the end of the log."}


def test_the_migration_backfill_builds_a_valid_chain(member):
    rows = write_rows(member, 4)
    for n, row in enumerate(rows):
        tamper(row, sequence=100 + n, hash="", previous_hash="")
    models.QuerySet.update(AuditChain.objects.all(), last_sequence=0, last_hash="")

    # The models exactly as the migration sees them (no append-only rules), like a real migrate.
    state = MigrationExecutor(connection).loader.project_state(("accounts", "0024_audit_log_hash_chain"))
    backfill(state.apps, None)

    assert [r.sequence for r in AuditLog.objects.order_by("created_at", "id")] == [1, 2, 3, 4]
    assert AuditLogService.verify()["valid"] is True
    assert AuditLog.record(AuditLog.LOGOUT, actor=member).sequence == 5


# ---- endpoints ----

AUDIT = "/api/v1/admin/audit-log/"


@pytest.fixture
def staff(db):
    return User.objects.create_user(
        username="support", email="support@axiomvault.ng", password="Str0ng-Passw0rd!", full_name="Kemi Support",
        is_staff=True, is_verified=True,
    )


def client_for(user):
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION="Bearer " + str(AccessToken.for_user(user)))
    return client


@pytest.fixture
def log(member, org, staff):
    other = Organisation.objects.create(name="Lekki Coop")
    rows = {
        "login": AuditLog.record(AuditLog.LOGIN, actor=member, details={"role": "super_admin"}),
        "case": AuditLog.record(AuditLog.CASE_CREATED, actor=member, target=member, details={"reference": "CASE-0001"}),
        "other": AuditLog.record(AuditLog.LOGIN, actor=staff, organisation=other),
        "platform": AuditLog.record(AuditLog.USER_CREATED, actor=staff, details={"note": "=SUM(A1)"}),
    }
    # Written with an old date (not changed afterwards, which verify would rightly flag).
    rows["old"] = AuditLog.objects.create(
        action=AuditLog.LOGOUT, actor=member, actor_email=member.email, organisation=org, organisation_name=org.name,
        created_at=timezone.now() - timedelta(days=10),
    )
    return rows


def refs(response):
    assert response.status_code == 200, response.content
    return [row["sequence"] for row in response.json()["results"]]


def test_list_is_newest_first_with_the_entry_shape(staff, log, member, org):
    response = client_for(staff).get(AUDIT)

    assert refs(response) == [5, 4, 3, 2, 1]
    entry = response.json()["results"][3]
    assert entry == {
        "id": str(log["case"].id), "sequence": 2, "created_at": entry["created_at"], "action": "case_created",
        "action_label": "Opened a case",
        "actor": {"id": str(member.id), "email": member.email, "full_name": "Ngozi Okafor"},
        "organisation": {"id": str(org.id), "name": "Ikeja Coop"},
        "target_type": "user", "target_id": str(member.id), "details": {"reference": "CASE-0001"}, "ip_address": None,
        "previous_hash": log["login"].hash, "hash": log["case"].hash,
    }


def test_list_filters(staff, log, member, org):
    client = client_for(staff)
    today = timezone.now().date()

    assert refs(client.get(AUDIT, {"action": "login"})) == [3, 1]
    assert refs(client.get(AUDIT, {"organisation": str(org.id)})) == [5, 2, 1]
    assert refs(client.get(AUDIT, {"organisation": "none"})) == [4]
    assert refs(client.get(AUDIT, {"actor": str(staff.id)})) == [4, 3]
    assert refs(client.get(AUDIT, {"target_type": "user", "target_id": str(member.id)})) == [2]
    assert refs(client.get(AUDIT, {"date_from": today.isoformat()})) == [4, 3, 2, 1]
    assert refs(client.get(AUDIT, {"date_to": (today - timedelta(days=5)).isoformat()})) == [5]
    assert refs(client.get(AUDIT, {"search": "LEKKI"})) == [3]
    assert refs(client.get(AUDIT, {"search": "ngozi@", "action": "logout"})) == [5]


@pytest.mark.parametrize("params, field", [
    ({"action": "hack"}, "action"), ({"organisation": "abc"}, "organisation"), ({"actor": "abc"}, "actor"),
    ({"date_from": "2026-13-01"}, "date_from"), ({"date_from": "2026-10-10", "date_to": "2026-10-01"}, "date_to"),
])
def test_bad_filters_are_400(staff, params, field):
    response = client_for(staff).get(AUDIT, params)

    assert response.status_code == 400
    assert field in response.json()


def test_retrieve_one_entry(staff, log):
    client = client_for(staff)

    assert client.get(f"{AUDIT}{log['platform'].id}/").json()["details"] == {"note": "=SUM(A1)"}
    assert client.get(f"{AUDIT}{uuid.uuid4()}/").status_code == 404
    assert client.get(f"{AUDIT}not-an-id/").status_code == 404


def test_actions_list(staff):
    actions = client_for(staff).get(f"{AUDIT}actions/").json()

    assert {"value": "audit_log_exported", "label": "Exported the audit log"} in actions
    assert len(actions) == len(AuditLog.ACTION_CHOICES)


@pytest.mark.parametrize("path", ["", "actions/", "verify/", "export/"])
def test_only_staff_can_read_the_log(path, member, staff, log):
    assert APIClient().get(AUDIT + path).status_code == 401
    refused = client_for(member).get(AUDIT + path)
    assert refused.status_code == 403
    assert refused.json()["code"] == "not_admin"
    User.objects.filter(pk=staff.pk).update(must_change_password=True)
    assert client_for(staff).get(AUDIT + path).status_code == 403
    assert not AuditLog.objects.filter(action=AuditLog.AUDIT_LOG_EXPORTED).exists()


def test_owner_can_read_the_log(db, log):
    owner = User.objects.create_superuser(username="owner", email="owner@axiomvault.ng", password="Str0ng-Passw0rd!")

    assert client_for(owner).get(AUDIT).status_code == 200


def test_verify_endpoint(staff, log):
    client = client_for(staff)

    intact = client.get(f"{AUDIT}verify/").json()
    assert (intact["valid"], intact["checked"], intact["last_sequence"], intact["problem"]) == (True, 5, 5, None)

    tamper(log["case"], details={"reference": "CASE-0099"})
    broken = client.get(f"{AUDIT}verify/").json()
    assert broken["valid"] is False
    assert (broken["checked"], broken["problem"]["sequence"], broken["problem"]["reason"]) == (1, 2, "altered")


def read_csv(response):
    return list(csv.reader(io.StringIO(b"".join(response.streaming_content).decode())))


def test_export_csv(staff, log):
    response = client_for(staff).get(f"{AUDIT}export/")

    assert response.status_code == 200
    assert response["Content-Type"] == "text/csv; charset=utf-8"
    assert response["Content-Disposition"] == f'attachment; filename="axiomvault-audit-log-{timezone.now().date().isoformat()}.csv"'
    rows = read_csv(response)
    assert rows[0] == ["sequence", "created_at", "action", "actor_email", "organisation_name", "target_type", "target_id", "ip_address", "details", "previous_hash", "hash"]
    assert [r[0] for r in rows[1:]] == ["5", "4", "3", "2", "1"]
    assert rows[2][2] == "user_created" and rows[2][8] == '{"note": "=SUM(A1)"}'
    assert rows[5][10] == log["login"].hash
    exported = AuditLog.objects.get(action=AuditLog.AUDIT_LOG_EXPORTED)
    assert (exported.actor, exported.organisation, exported.details) == (staff, None, {"filters": {}, "rows": 5})
    assert AuditLogService.verify()["valid"] is True


def test_export_applies_filters_and_neutralises_formulas(staff, member):
    AuditLog.record(AuditLog.LOGIN, actor=member, details={"x": 1})
    AuditLog.objects.create(action=AuditLog.LOGIN, actor_email="=HYPERLINK(\"http://evil\")", target_id="+1", organisation_name="@org")

    rows = read_csv(client_for(staff).get(f"{AUDIT}export/", {"action": "login", "search": "evil"}))

    assert len(rows) == 2
    assert rows[1][3:7] == ["'=HYPERLINK(\"http://evil\")", "'@org", "", "'+1"]
    assert AuditLog.objects.get(action=AuditLog.AUDIT_LOG_EXPORTED).details == {"filters": {"action": "login", "search": "evil"}, "rows": 1}


def test_export_bad_filter_writes_nothing(staff):
    assert client_for(staff).get(f"{AUDIT}export/", {"date_from": "nope"}).status_code == 400
    assert not AuditLog.objects.filter(action=AuditLog.AUDIT_LOG_EXPORTED).exists()