"""Every person in the alert and case APIs carries their profile picture (feature 17, step 3)."""

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile

from fraud.test_cases import CASES, client_for, make_alert, member, open_case, org, owner  # noqa: F401 (fixtures)

User = get_user_model()
OWNER_PIC = "https://res.cloudinary.com/demo/image/upload/v1/axiomvault/avatars/owner.png"
MEMBER_PIC = "https://res.cloudinary.com/demo/image/upload/v1/axiomvault/avatars/member.png"


def test_alert_people_carry_pictures(owner, member, org):
    User.objects.filter(pk=owner.pk).update(profile_picture_url=OWNER_PIC)
    make_alert(org, 1)
    client = client_for(owner)
    client.put("/api/v1/fraud/alerts/ALT-0001/reviewer/", {"reviewer_id": str(member.id)}, format="json")
    client.post("/api/v1/fraud/alerts/ALT-0001/notes/", {"text": "Check approvals."}, format="json")

    detail = client.get("/api/v1/fraud/alerts/ALT-0001/").json()
    listed = client.get("/api/v1/fraud/alerts/").json()["results"][0]
    notes = client.get("/api/v1/fraud/alerts/ALT-0001/notes/").json()["results"]
    history = client.get("/api/v1/fraud/alerts/ALT-0001/history/").json()["results"]

    assert detail["assigned_reviewer"]["profile_picture"] is None  # Member has none yet.
    assert listed["assigned_reviewer"]["full_name"] == "Ada Obi"
    assert notes[0]["author"]["profile_picture"] == OWNER_PIC
    assert history[-1]["actor"]["profile_picture"] == OWNER_PIC

    User.objects.filter(pk=member.pk).update(profile_picture_url=MEMBER_PIC)
    assert client.get("/api/v1/fraud/alerts/ALT-0001/").json()["assigned_reviewer"]["profile_picture"] == MEMBER_PIC


def test_case_people_carry_pictures(owner, member, org):
    User.objects.filter(pk=owner.pk).update(profile_picture_url=OWNER_PIC)
    User.objects.filter(pk=member.pk).update(profile_picture_url=MEMBER_PIC)
    make_alert(org, 1)
    open_case(owner, "ALT-0001", investigator_id=str(member.id))
    client = client_for(owner)
    case = CASES + "CASE-0001/"
    client.put(case + "assignment/", {"reviewer_id": str(owner.id)}, format="json")
    client.post(case + "comments/", {"text": "Called the bank."}, format="json")
    upload = client.post("/api/v1/files/", {"file": SimpleUploadedFile("s.csv", b"a,b")}, format="multipart").json()
    client.post(case + "evidence/", {"file_id": upload["id"]}, format="json")
    client.post(case + "escalate/", {"reason": "legal_risk", "escalate_to_id": str(member.id), "priority": "high"}, format="json")

    detail = client.get(case).json()
    assert detail["investigator"]["profile_picture"] == MEMBER_PIC
    assert detail["reviewer"]["profile_picture"] == OWNER_PIC
    assert detail["created_by"]["profile_picture"] == OWNER_PIC
    assert detail["escalation"]["escalated_to"]["profile_picture"] == MEMBER_PIC
    assert client.get(CASES).json()["results"][0]["investigator"]["profile_picture"] == MEMBER_PIC
    assert client.get(case + "comments/").json()["results"][0]["author"]["profile_picture"] == OWNER_PIC
    evidence = client.get(case + "evidence/").json()["results"][0]
    assert evidence["added_by"]["profile_picture"] == OWNER_PIC and evidence["file"]["uploaded_by"]["profile_picture"] == OWNER_PIC
    assert all(e["actor"]["profile_picture"] == OWNER_PIC for e in client.get(case + "history/").json()["results"])
    assert upload["uploaded_by"]["profile_picture"] == OWNER_PIC
