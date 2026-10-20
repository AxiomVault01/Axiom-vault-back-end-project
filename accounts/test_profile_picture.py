import io
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from accounts.models import AuditLog, Organisation
from config.file_storage import StorageUnavailable

User = get_user_model()
PICTURE = "/api/v1/me/picture/"


def image_bytes(fmt="PNG", size=(64, 64)):
    buffer = io.BytesIO()
    Image.new("RGB", size, (31, 107, 82)).save(buffer, format=fmt)
    return buffer.getvalue()


@pytest.fixture
def org(db):
    return Organisation.objects.create(name="Ikeja Coop")


@pytest.fixture
def owner(org):
    return User.objects.create_user(
        username="owner", email="owner@ikejacoop.ng", password="Str0ng-Passw0rd!", full_name="Ngozi Okafor",
        organisation=org, account_type="super_admin", is_verified=True,
    )


def client_for(user):
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION="Bearer " + str(AccessToken.for_user(user)))
    return client


def put_picture(user, data=None, name="me.png"):
    data = image_bytes() if data is None else data
    return client_for(user).put(PICTURE, {"file": SimpleUploadedFile(name, data)}, format="multipart")


@pytest.mark.parametrize("fmt", ["PNG", "JPEG", "WEBP"])
def test_upload_a_picture(owner, fake_storage, fmt):
    response = put_picture(owner, image_bytes(fmt), name=f"me.{fmt.lower()}")

    assert response.status_code == 200
    owner.refresh_from_db()
    assert response.json() == {"profile_picture": owner.profile_picture_url}
    assert owner.profile_picture_public_id.startswith(f"axiomvault/avatars/{owner.id}/")
    assert fake_storage.files[owner.profile_picture_public_id] == image_bytes(fmt)
    assert AuditLog.objects.get(action=AuditLog.PROFILE_PICTURE_UPDATED).details == {"replaced": False}


def test_replacing_deletes_the_old_picture(owner, fake_storage):
    put_picture(owner)
    owner.refresh_from_db()
    old_id, old_url = owner.profile_picture_public_id, owner.profile_picture_url

    response = put_picture(owner, image_bytes("JPEG"))

    owner.refresh_from_db()
    assert response.status_code == 200 and owner.profile_picture_url != old_url
    assert old_id in fake_storage.deleted and old_id not in fake_storage.files
    assert AuditLog.objects.filter(action=AuditLog.PROFILE_PICTURE_UPDATED).latest("created_at").details == {"replaced": True}


def test_remove_the_picture(owner, fake_storage):
    put_picture(owner)
    owner.refresh_from_db()
    old_id = owner.profile_picture_public_id

    response = client_for(owner).delete(PICTURE)

    owner.refresh_from_db()
    assert response.status_code == 204
    assert owner.profile_picture_url == "" and owner.profile_picture_public_id == ""
    assert old_id in fake_storage.deleted
    assert AuditLog.objects.filter(action=AuditLog.PROFILE_PICTURE_REMOVED).count() == 1
    # Removing when there is none is fine and records nothing.
    assert client_for(owner).delete(PICTURE).status_code == 204
    assert AuditLog.objects.filter(action=AuditLog.PROFILE_PICTURE_REMOVED).count() == 1


@pytest.mark.parametrize(
    "data, name",
    [
        (b"this is not an image", "fake.png"),  # Renamed text.
        (b"%PDF-1.4 statement", "statement.pdf"),
        ("GIF", "anim.gif"),
        ("BMP", "old.bmp"),
        ("TRUNCATED", "broken.png"),
    ],
)
def test_only_jpeg_png_webp(owner, fake_storage, data, name):
    if data in ("GIF", "BMP"):
        data = image_bytes(data)
    elif data == "TRUNCATED":
        data = image_bytes("PNG")[:60]

    response = put_picture(owner, data, name)

    assert response.status_code == 400 and response.json() == {"file": ["Upload a JPEG, PNG, or WebP image."]}
    assert not fake_storage.files


def test_no_file_or_empty_file(owner):
    assert client_for(owner).put(PICTURE, {}, format="multipart").json() == {"file": ["No file was submitted."]}
    assert put_picture(owner, b"").json() == {"file": ["The submitted file is empty."]}


def test_over_2_mb_is_413(owner, fake_storage):
    big = image_bytes("PNG") + b"\0" * (2 * 1024 * 1024)

    response = put_picture(owner, big)

    assert response.status_code == 413 and response.json() == {"error": "The picture is larger than 2 MB."}
    assert not fake_storage.files


def test_storage_down_keeps_the_old_picture(owner, fake_storage):
    put_picture(owner)
    owner.refresh_from_db()
    old_url = owner.profile_picture_url

    with patch("config.file_storage.store_public_image", side_effect=StorageUnavailable("down")):
        response = put_picture(owner, image_bytes("JPEG"))

    owner.refresh_from_db()
    assert response.status_code == 503 and "Nothing changed" in response.json()["error"]
    assert owner.profile_picture_url == old_url and not fake_storage.deleted


def test_a_failed_save_removes_the_new_picture(owner, fake_storage):
    with patch("accounts.me_viewsets.AuditLog.record", side_effect=RuntimeError("database down")):
        with pytest.raises(RuntimeError):
            put_picture(owner)

    owner.refresh_from_db()
    assert owner.profile_picture_url == "" and fake_storage.deleted and not fake_storage.files


def test_auth_rules(owner, db):
    assert APIClient().put(PICTURE, {}, format="multipart").status_code == 401
    assert APIClient().delete(PICTURE).status_code == 401
    staff = User.objects.create_user(username="staff", email="staff@axiomvault.ng", password="Str0ng-Passw0rd!", is_staff=True, is_verified=True)
    assert put_picture(staff).status_code == 200  # Staff belong to no organisation but have a picture too.
    User.objects.filter(pk=owner.pk).update(must_change_password=True)
    owner.refresh_from_db()
    assert put_picture(owner).status_code == 403


# ---- step 2: shown at login and on the team ----


def test_login_and_team_show_the_picture(owner, org):
    member = User.objects.create_user(
        username="ada", email="ada@ikejacoop.ng", password="Str0ng-Passw0rd!", full_name="Ada Obi", organisation=org,
        account_type="team_member", is_verified=True,
    )
    login = lambda: APIClient().post("/api/v1/auth/login/", {"email": "owner@ikejacoop.ng", "password": "Str0ng-Passw0rd!", "role": "super_admin"}, format="json").json()
    assert login()["user"]["profile_picture"] is None

    url = put_picture(owner).json()["profile_picture"]
    put_picture(member)
    member.refresh_from_db()

    assert login()["user"]["profile_picture"] == url
    rows = client_for(owner).get("/api/v1/team/members/").json()["results"]
    assert [r["profile_picture"] for r in rows if r["email"] == "ada@ikejacoop.ng"] == [member.profile_picture_url]
