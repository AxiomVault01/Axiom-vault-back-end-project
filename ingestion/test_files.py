from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from accounts.models import AuditLog, Organisation
from config.file_storage import StorageUnavailable
from config.file_storage import download_url as real_download_url  # Imported before the fake replaces it.
from ingestion.models import StoredFile

User = get_user_model()
FILES = "/api/v1/files/"


@pytest.fixture
def org(db):
    return Organisation.objects.create(name="Ikeja Coop")


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
        username="boss", email="boss@lekkicoop.ng", password="Str0ng-Passw0rd!", organisation=other,
        account_type="super_admin", is_verified=True,
    )


def client_for(user):
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION="Bearer " + str(AccessToken.for_user(user)))
    return client


def upload(user, name="statement.pdf", content=b"%PDF-1.4 bank statement", content_type="application/pdf"):
    return client_for(user).post(FILES, {"file": SimpleUploadedFile(name, content, content_type=content_type)}, format="multipart")


def test_upload_any_file(member, org, fake_storage):
    response = upload(member)

    assert response.status_code == 201
    body = response.json()
    stored = StoredFile.objects.get()
    assert body["id"] == str(stored.id)
    assert body["file_name"] == "statement.pdf" and body["content_type"] == "application/pdf"
    assert body["size"] == len(b"%PDF-1.4 bank statement")
    assert body["uploaded_by"] == {"id": str(member.id), "full_name": "Ada Obi", "profile_picture": None}
    assert body["url"].startswith("https://storage.test/") and body["url_expires_at"]
    assert "public_id" not in body
    assert stored.public_id == f"axiomvault/{org.id}/files/{stored.id}.pdf"
    assert fake_storage.files[stored.public_id] == b"%PDF-1.4 bank statement"
    assert AuditLog.objects.get(action=AuditLog.FILE_UPLOADED).details == {"file_name": "statement.pdf", "size": 23}


@pytest.mark.parametrize("name, content_type", [("evidence.eml", "message/rfc822"), ("photo.JPG", "image/jpeg"), ("notes", "")])
def test_any_type_is_accepted(member, name, content_type):
    response = upload(member, name=name, content=b"data", content_type=content_type)

    assert response.status_code == 201
    assert response.json()["content_type"] == (content_type or "application/octet-stream")


def test_no_file_or_empty_file_is_400(member):
    assert client_for(member).post(FILES, {}, format="multipart").json() == {"file": ["No file was submitted."]}
    assert upload(member, content=b"").json() == {"file": ["The submitted file is empty."]}
    assert not StoredFile.objects.exists()


def test_over_10_mb_is_413(member, fake_storage):
    response = upload(member, content=b"x" * (10 * 1024 * 1024 + 1))

    assert response.status_code == 413 and response.json() == {"error": "The file is larger than 10 MB."}
    assert not StoredFile.objects.exists() and not fake_storage.files


def test_storage_down_saves_nothing(member):
    with patch("config.file_storage.store_private", side_effect=StorageUnavailable("down")):
        response = upload(member)

    assert response.status_code == 503 and "Nothing was saved" in response.json()["error"]
    assert not StoredFile.objects.exists()


def test_a_failed_save_removes_the_stored_file(member, fake_storage):
    with patch("ingestion.file_viewsets.AuditLog.record", side_effect=RuntimeError("database down")):
        with pytest.raises(RuntimeError):
            upload(member)

    assert not StoredFile.objects.exists()
    assert fake_storage.deleted and not fake_storage.files


def test_read_gives_a_fresh_link(member):
    file_id = upload(member).json()["id"]

    response = client_for(member).get(f"{FILES}{file_id}/")

    assert response.status_code == 200
    assert response.json()["id"] == file_id and response.json()["url"].startswith("https://storage.test/")


def test_other_organisations_files_are_404(member, outsider):
    file_id = upload(member).json()["id"]
    client = client_for(outsider)

    assert client.get(f"{FILES}{file_id}/").status_code == 404
    assert client.get(f"{FILES}00000000-0000-0000-0000-000000000000/").status_code == 404
    assert client.get(f"{FILES}not-an-id/").status_code == 404


def test_files_need_a_token_a_changed_password_and_an_organisation(member, db):
    assert APIClient().post(FILES, {}, format="multipart").status_code == 401
    User.objects.filter(pk=member.pk).update(must_change_password=True)
    member.refresh_from_db()
    assert upload(member).status_code == 403
    staff = User.objects.create_user(username="staff", email="staff@axiomvault.ng", password="Str0ng-Passw0rd!", is_staff=True, is_verified=True)
    assert upload(staff).status_code == 403
    assert not StoredFile.objects.exists()


def test_real_download_link_is_signed_and_expires(monkeypatch):
    monkeypatch.setenv("CLOUDINARY_URL", "cloudinary://123456:secret@demo-cloud")
    import cloudinary

    cloudinary.reset_config()
    try:
        url, expires_at = real_download_url("axiomvault/org/files/abc.pdf", "statement.pdf", expires_in=600)
    finally:
        monkeypatch.delenv("CLOUDINARY_URL")
        cloudinary.reset_config()

    assert url.startswith("https://api.cloudinary.com/v1_1/demo-cloud/raw/download?")
    for part in ("attachment=1", f"expires_at={expires_at}", "type=authenticated", "signature=", "api_key=123456"):
        assert part in url
    assert "secret" not in url


def test_no_storage_settings_means_no_link(monkeypatch):
    monkeypatch.delenv("CLOUDINARY_URL", raising=False)

    with pytest.raises(StorageUnavailable):
        real_download_url("x", "y")
