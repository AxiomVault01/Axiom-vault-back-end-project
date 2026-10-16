from unittest.mock import patch

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command

from accounts.demo import seed
from accounts.models import AuditLog, Organisation
from config.file_storage import StorageUnavailable
from fraud.models import Case, CaseEvent, CaseEvidence
from fraud.test_cases import CASES, client_for, make_alert, member, open_case, org, outsider, owner  # noqa: F401 (fixtures)
from ingestion.models import StoredFile


def upload_file(user, name="statement.pdf", content=b"bank statement"):
    response = client_for(user).post("/api/v1/files/", {"file": SimpleUploadedFile(name, content)}, format="multipart")
    assert response.status_code == 201
    return response.json()["id"]


@pytest.fixture
def case(owner, org):
    make_alert(org, 1)
    open_case(owner, "ALT-0001")
    return Case.objects.get(organisation=org)


EVIDENCE = CASES + "CASE-0001/evidence/"


# ---- 16d: attach, list, remove ----


def test_attach_evidence(owner, member, case):
    file_id = upload_file(member)

    response = client_for(owner).post(EVIDENCE, {"file_id": file_id}, format="json")

    assert response.status_code == 201
    body = response.json()
    assert body["source"] == "evidence" and body["added_by"]["full_name"] == "Ngozi Okafor" and body["added_at"]
    assert body["file"]["id"] == file_id and body["file"]["file_name"] == "statement.pdf"
    assert body["file"]["uploaded_by"]["full_name"] == "Ada Obi" and body["file"]["url"].startswith("https://storage.test/")
    assert CaseEvent.objects.get(type="evidence_added").detail == "statement.pdf"
    assert AuditLog.objects.get(action=AuditLog.CASE_EVIDENCE_ADDED).details["file_id"] == file_id
    detail = client_for(owner).get(CASES + "CASE-0001/").json()
    assert detail["evidence_count"] == 1 and detail["alerts_count"] == 1
    assert client_for(owner).get(CASES).json()["results"][0]["evidence_count"] == 1


def test_list_is_newest_first_and_paginated(owner, case):
    first, second = upload_file(owner, "a.pdf"), upload_file(owner, "b.png")
    client = client_for(owner)
    client.post(EVIDENCE, {"file_id": first}, format="json")
    client.post(EVIDENCE, {"file_id": second}, format="json")
    CaseEvidence.objects.filter(file_id=first).update(created_at=CaseEvidence.objects.get(file_id=second).created_at.replace(year=2025))

    body = client.get(EVIDENCE, {"page_size": 1}).json()

    assert body["count"] == 2 and body["next"]
    assert body["results"][0]["file"]["file_name"] == "b.png"


def test_counts_stay_right_with_several_alerts_and_files(owner, org):
    make_alert(org, 1)
    make_alert(org, 2)
    open_case(owner, "ALT-0001", "ALT-0002")
    client = client_for(owner)
    for name in ("a.pdf", "b.pdf", "c.pdf"):
        client.post(EVIDENCE, {"file_id": upload_file(owner, name)}, format="json")

    row = client.get(CASES).json()["results"][0]

    assert (row["alerts_count"], row["evidence_count"]) == (2, 3)


def test_alert_detail_shows_its_case_evidence(owner, org, case):
    make_alert(org, 2)
    client = client_for(owner)
    client.post(EVIDENCE, {"file_id": upload_file(owner)}, format="json")

    assert [e["file"]["file_name"] for e in client.get("/api/v1/fraud/alerts/ALT-0001/").json()["evidence"]] == ["statement.pdf"]
    assert client.get("/api/v1/fraud/alerts/ALT-0002/").json()["evidence"] == []


def test_attach_refusals(owner, outsider, case):
    client = client_for(owner)
    mine = upload_file(owner)
    client.post(EVIDENCE, {"file_id": mine}, format="json")

    assert client.post(EVIDENCE, {"file_id": mine}, format="json").json() == {"file_id": ["This file is already evidence on CASE-0001."]}
    theirs = upload_file(outsider)
    assert client.post(EVIDENCE, {"file_id": theirs}, format="json").json() == {"file_id": ["No file with this ID in your organisation."]}
    assert client.post(EVIDENCE, {"file_id": "00000000-0000-0000-0000-000000000000"}, format="json").status_code == 400
    assert client.post(EVIDENCE, {"file_id": "nope"}, format="json").status_code == 400
    assert client.post(EVIDENCE, {}, format="json").status_code == 400
    assert CaseEvidence.objects.count() == 1


def test_other_organisations_cannot_see_or_change_evidence(owner, outsider, case):
    client_for(owner).post(EVIDENCE, {"file_id": upload_file(owner)}, format="json")
    evidence_id = CaseEvidence.objects.get().pk
    client = client_for(outsider)

    assert client.get(EVIDENCE).status_code == 404
    assert client.post(EVIDENCE, {"file_id": upload_file(outsider)}, format="json").status_code == 404
    assert client.delete(f"{EVIDENCE}{evidence_id}/").status_code == 404
    assert CaseEvidence.objects.count() == 1


def test_remove_evidence_keeps_the_file(owner, case, fake_storage):
    file_id = upload_file(owner)
    client = client_for(owner)
    evidence_id = client.post(EVIDENCE, {"file_id": file_id}, format="json").json()["id"]

    response = client.delete(f"{EVIDENCE}{evidence_id}/")

    assert response.status_code == 204
    assert not CaseEvidence.objects.exists() and StoredFile.objects.filter(pk=file_id).exists() and not fake_storage.deleted
    assert CaseEvent.objects.get(type="evidence_removed").detail == "statement.pdf"
    assert AuditLog.objects.filter(action=AuditLog.CASE_EVIDENCE_REMOVED).count() == 1
    assert client.get(CASES + "CASE-0001/").json()["evidence_count"] == 0
    # Removing again, or a made-up ID, is 404.
    assert client.delete(f"{EVIDENCE}{evidence_id}/").status_code == 404
    assert client.delete(f"{EVIDENCE}not-an-id/").status_code == 404


def test_evidence_list_loads_when_storage_is_down(owner, case):
    client = client_for(owner)
    client.post(EVIDENCE, {"file_id": upload_file(owner)}, format="json")

    with patch("config.file_storage.download_url", side_effect=StorageUnavailable("down")):
        body = client.get(EVIDENCE).json()

    assert body["results"][0]["file"]["url"] is None and body["results"][0]["file"]["url_expires_at"] is None


# ---- 16e: escalation supporting documents ----


def escalate(user, member, **extra):
    body = {"reason": "complex_fraud", "escalate_to_id": str(member.id), "priority": "high", **extra}
    return client_for(user).post(CASES + "CASE-0001/escalate/", body, format="json")


def test_escalate_with_documents(owner, member, case):
    sheet, csv = upload_file(owner, "payment-run.xlsx"), upload_file(owner, "ledger.CSV")
    client_for(owner).post(EVIDENCE, {"file_id": csv}, format="json")  # Already evidence: stays as it is.

    response = escalate(owner, member, document_ids=[sheet, csv, sheet])

    assert response.status_code == 200
    assert response.json()["status"] == "escalated" and response.json()["evidence_count"] == 2
    assert dict(CaseEvidence.objects.values_list("file_id", "source")) == {
        StoredFile.objects.get(pk=sheet).pk: "escalation", StoredFile.objects.get(pk=csv).pk: "evidence",
    }
    assert AuditLog.objects.get(action=AuditLog.CASE_ESCALATED).details["documents"] == [sheet, csv]


@pytest.mark.parametrize(
    "documents, message",
    [
        (["statement.pdf"], "Supporting documents must be CSV, XLS, or XLSX files."),
        (["unknown"], "No file with this ID in your organisation."),
    ],
)
def test_escalation_document_refusals(owner, member, case, documents, message):
    ids = ["00000000-0000-0000-0000-000000000000" if d == "unknown" else upload_file(owner, d) for d in documents]

    response = escalate(owner, member, document_ids=ids)

    assert response.status_code == 400 and response.json() == {"document_ids": [message]}
    assert Case.objects.get().status == "new" and not CaseEvidence.objects.exists()


def test_at_most_10_escalation_documents(owner, member, case):
    ids = [upload_file(owner, f"sheet{n}.xlsx") for n in range(11)]

    assert escalate(owner, member, document_ids=ids).status_code == 400
    assert not CaseEvidence.objects.exists()


def test_another_organisations_document_is_refused(owner, member, outsider, case):
    assert escalate(owner, member, document_ids=[upload_file(outsider, "theirs.xlsx")]).json() == {"document_ids": ["No file with this ID in your organisation."]}


# ---- demo cleanup ----


def test_clear_demo_removes_demo_files_and_evidence(db, fake_storage, django_capture_on_commit_callbacks):
    seed()
    demo_admin = Organisation.objects.get(is_demo=True).users.get(account_type="super_admin")
    file_id = upload_file(demo_admin)
    client_for(demo_admin).post(EVIDENCE, {"file_id": file_id}, format="json")
    public_id = StoredFile.objects.get(pk=file_id).public_id

    with patch("sys.stdout"), django_capture_on_commit_callbacks(execute=True):
        call_command("clear_demo", "--yes")

    assert not StoredFile.objects.exists() and not CaseEvidence.objects.exists()
    assert public_id in fake_storage.deleted
