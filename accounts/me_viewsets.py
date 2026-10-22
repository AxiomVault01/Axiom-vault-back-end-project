"""The logged-in user's own account: profile (feature 17) and profile picture (feature 32)."""

import io
import secrets

from django.db import transaction
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiExample, OpenApiResponse, extend_schema, inline_serializer
from PIL import Image
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response

from config import file_storage
from config.file_storage import StorageUnavailable

from .models import AuditLog, UserSettings
from .permissions import PASSWORD_CHANGE_REQUIRED_MESSAGE
from .serializers import PHONE_NUMBER_MESSAGE, TIME_ZONE_MESSAGE, ProfileSerializer, ProfileUpdateSerializer, SettingsSerializer

MAX_PICTURE_BYTES = 2 * 1024 * 1024
PICTURE_FORMATS = {"JPEG", "PNG", "WEBP"}
PICTURE_TOO_LARGE_MESSAGE = "The picture is larger than 2 MB."
PICTURE_TYPE_MESSAGE = "Upload a JPEG, PNG, or WebP image."
STORAGE_UNAVAILABLE_MESSAGE = "The picture could not be stored right now. Nothing changed. Try again in a few minutes."


class PictureUploadSerializer(serializers.Serializer):
    file = serializers.FileField(help_text="A JPEG, PNG, or WebP image, at most 2 MB. Checked from its content, not its name.", label="Picture")


PICTURE_RESPONSE = inline_serializer(
    name="ProfilePicture",
    fields={"profile_picture": serializers.URLField(help_text="Public URL of the picture, for `<img src>`. Changes every upload.")},
)
PICTURE_URL = "https://res.cloudinary.com/<cloud>/image/upload/v1792080000/axiomvault/avatars/2d089ad3-9545-4611-a9dc-731c3a81879f/9f2c4e1a7b3d5c60.png"
AUTH_NOTE = (
    "**Authentication:** `Authorization: Bearer <access_token>` of any logged-in user (super admin, team member, or "
    "staff). It always changes the caller's own picture."
)
UNAUTHORIZED = OpenApiResponse(
    response=OpenApiTypes.OBJECT,
    description="No access token, or it is expired or invalid.",
    examples=[OpenApiExample("No token", value={"detail": "Authentication credentials were not provided."})],
)
FORBIDDEN = OpenApiResponse(
    response=OpenApiTypes.OBJECT,
    description="Temporary password not changed yet.",
    examples=[OpenApiExample("Temporary password", value={"error": PASSWORD_CHANGE_REQUIRED_MESSAGE, "code": "password_change_required"})],
)


def picture_format(data: bytes):
    """The image format read from the bytes ("JPEG", "PNG", "WEBP", ...), or None if it is not an image."""
    try:
        with Image.open(io.BytesIO(data)) as image:
            image.verify()  # Checks the file is a complete, readable image without decoding every pixel.
            return image.format
    except Exception:
        return None


PROFILE_EXAMPLE = {
    "id": "2d089ad3-9545-4611-a9dc-731c3a81879f",
    "email": "ngozi@ikejacoop.ng",
    "full_name": "Ngozi Okafor",
    "department": "internal_audit",
    "phone_number": "+2348031234567",
    "role": "",
    "account_type": "super_admin",
    "organisation": {"id": "b3f1c2d4-5e6f-4a7b-8c9d-0e1f2a3b4c5d", "name": "Ikeja Cooperative"},
    "profile_picture": PICTURE_URL,
    "is_staff": False,
    "date_joined": "2026-10-01T09:00:00Z",
    "last_login": "2026-10-20T08:15:00Z",
}
PROFILE_NOTE = (
    "**Editable here:** `full_name`, `department`, `phone_number`. **Read-only:** `email`, `role`, "
    "`account_type`, `organisation` (a super admin changes a member's email and role on the Team screen; AxiomVault "
    "staff manage organisations), `is_staff`, dates. The picture has its own endpoint, `PUT /api/v1/me/picture/`, "
    "and the password `POST /api/v1/auth/change-password/`."
)


SETTINGS_EXAMPLE = {
    "critical_risk_alerts": True,
    "high_risk_alerts": True,
    "daily_summary_reports": False,
    "system_updates": True,
    "language": "en-US",
    "time_zone": "Africa/Lagos",
    "date_format": "DD/MM/YYYY",
    "updated_at": "2026-10-21T09:00:00Z",
}


class MeViewSet(viewsets.ViewSet):
    """Endpoints about the caller's own account."""

    @extend_schema(
        tags=["My account"],
        summary="My profile",
        description=(
            "**Profile screen.** The logged-in user's own profile: name, email, department, phone, job title, account "
            "type, Cooperative, picture, and dates. Works for super admins, team members, and AxiomVault staff "
            "(`organisation` and `account_type` are empty for staff).\n\n" + PROFILE_NOTE + "\n\n" + AUTH_NOTE
        ),
        responses={200: OpenApiResponse(response=ProfileSerializer, examples=[OpenApiExample("Profile", value=PROFILE_EXAMPLE)]), 401: UNAUTHORIZED, 403: FORBIDDEN},
    )
    def retrieve_profile(self, request):
        return Response(ProfileSerializer(request.user).data)

    @extend_schema(
        tags=["My account"],
        summary="Edit my profile",
        description=(
            "**Save on the Profile screen.** Changes only the fields sent, all optional:\n"
            "- `full_name`: 1 to 255 characters, spaces trimmed\n"
            "- `department`: `internal_audit`, `finance`, `compliance`, `risk_management`, `human_resources`, `others`\n"
            "- `phone_number`: 7 to 15 digits with an optional leading `+`; spaces and hyphens are removed; `\"\"` "
            "clears it\n\n"
            "Any other key (for example `email` or `role`) is ignored, never an error. 200 returns the whole updated "
            "profile; update the name shown in the header from it. Changes are recorded in the audit log.\n\n"
            + PROFILE_NOTE + "\n\n" + AUTH_NOTE
        ),
        request=ProfileUpdateSerializer,
        examples=[OpenApiExample("Edit", value={"full_name": "Ngozi A. Okafor", "department": "compliance", "phone_number": "+234 803 123 4567"}, request_only=True)],
        responses={
            200: OpenApiResponse(response=ProfileSerializer, examples=[OpenApiExample("Updated", value={**PROFILE_EXAMPLE, "full_name": "Ngozi A. Okafor", "department": "compliance"})]),
            400: OpenApiResponse(
                response=OpenApiTypes.OBJECT,
                examples=[
                    OpenApiExample("Blank name", value={"full_name": ["This field may not be blank."]}),
                    OpenApiExample("Unknown department", value={"department": ['"sales" is not a valid choice.']}),
                    OpenApiExample("Bad phone", value={"phone_number": [PHONE_NUMBER_MESSAGE]}),
                ],
            ),
            401: UNAUTHORIZED,
            403: FORBIDDEN,
        },
    )
    def update_profile(self, request):
        serializer = ProfileUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = request.user
        changed = [name for name, value in serializer.validated_data.items() if getattr(user, name) != value]
        if changed:
            with transaction.atomic():
                for name in changed:
                    setattr(user, name, serializer.validated_data[name])
                user.save(update_fields=changed)
                AuditLog.record(AuditLog.PROFILE_UPDATED, actor=user, target=user, request=request, details={"fields": changed})
        return Response(ProfileSerializer(user).data)

    @extend_schema(
        tags=["My account"],
        summary="My settings",
        description=(
            "**Settings screen: Notification Preferences and System Preferences.** The logged-in user's own "
            "preferences. The first read creates them with the defaults: Critical Risk Alerts on, High Risk Alerts on, "
            "Daily Summary Reports off, System Updates on, language `en-US`, time zone `Africa/Lagos`, date format "
            "`DD/MM/YYYY`.\n\n"
            "**What they do today:** the frontend uses language, time zone, and date format to display data (the API "
            "always sends times as ISO 8601 UTC). The notification switches are saved for the coming notification "
            "feature; no notifications are sent yet.\n\n" + AUTH_NOTE
        ),
        methods=["GET"],
        responses={200: OpenApiResponse(response=SettingsSerializer, examples=[OpenApiExample("Defaults", value=SETTINGS_EXAMPLE)]), 401: UNAUTHORIZED, 403: FORBIDDEN},
    )
    @extend_schema(
        tags=["My account"],
        summary="Save my settings",
        description=(
            "**Save Preferences** (and each notification switch). Changes only the fields sent, all optional:\n"
            "- `critical_risk_alerts`, `high_risk_alerts`, `daily_summary_reports`, `system_updates`: `true` or `false`\n"
            "- `language`: `en-US`, `en-GB`, or `es`\n"
            "- `time_zone`: an IANA time zone name such as `Africa/Lagos` or `Europe/London`\n"
            "- `date_format`: `DD/MM/YYYY`, `MM/DD/YYYY`, or `YYYY-MM-DD`\n\n"
            "Other keys are ignored. 200 returns all settings. If any value is invalid, nothing is saved (400). "
            "Changes are recorded in the audit log.\n\n" + AUTH_NOTE
        ),
        methods=["PATCH"],
        request=SettingsSerializer,
        examples=[OpenApiExample("Turn on daily reports, UK English", value={"daily_summary_reports": True, "language": "en-GB"}, request_only=True)],
        responses={
            200: OpenApiResponse(response=SettingsSerializer, examples=[OpenApiExample("Saved", value={**SETTINGS_EXAMPLE, "daily_summary_reports": True, "language": "en-GB"})]),
            400: OpenApiResponse(
                response=OpenApiTypes.OBJECT,
                examples=[
                    OpenApiExample("Unknown language", value={"language": ['"fr" is not a valid choice.']}),
                    OpenApiExample("Bad time zone", value={"time_zone": [TIME_ZONE_MESSAGE]}),
                    OpenApiExample("Not a boolean", value={"system_updates": ["Must be a valid boolean."]}),
                ],
            ),
            401: UNAUTHORIZED,
            403: FORBIDDEN,
        },
    )
    @action(detail=False, methods=["get", "patch"], url_path="settings")
    def user_settings(self, request):
        user = request.user
        settings, _ = UserSettings.objects.get_or_create(user=user)
        if request.method == "GET":
            return Response(SettingsSerializer(settings).data)
        serializer = SettingsSerializer(settings, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        changed = [name for name, value in serializer.validated_data.items() if getattr(settings, name) != value]
        if changed:
            with transaction.atomic():
                serializer.save()
                AuditLog.record(AuditLog.SETTINGS_UPDATED, actor=user, target=user, request=request, details={"fields": changed})
        return Response(SettingsSerializer(settings).data)

    @extend_schema(
        tags=["My account"],
        summary="Upload or replace my profile picture",
        description=(
            "**Profile picture** (profile screen and the avatar shown across the app). Uploads a new picture for the "
            "logged-in user and replaces the old one, which is deleted.\n\n"
            "**Request:** `multipart/form-data` with one field, `file`: a **JPEG, PNG, or WebP** image, at most "
            "**2 MB**. The type is read from the file's content, so a renamed file of another type is refused. Crop "
            "or resize in the browser before uploading if needed.\n\n"
            "**200** returns `profile_picture`, the picture's public URL; use it directly in `<img src>`. The same URL "
            "is also returned at login (`user.profile_picture`) and on team members. The URL changes on every upload, "
            "so browsers never show a stale picture.\n\n"
            "**Errors:** no file or an empty file is 400; another type (GIF, BMP, PDF, …) is 400; over 2 MB is 413; "
            "503 means storage is down and the old picture is kept. Every change is recorded in the audit log.\n\n" + AUTH_NOTE
        ),
        request={"multipart/form-data": PictureUploadSerializer},
        responses={
            200: OpenApiResponse(response=PICTURE_RESPONSE, examples=[OpenApiExample("Uploaded", value={"profile_picture": PICTURE_URL})]),
            400: OpenApiResponse(
                response=OpenApiTypes.OBJECT,
                examples=[
                    OpenApiExample("Wrong type", value={"file": [PICTURE_TYPE_MESSAGE]}),
                    OpenApiExample("No file", value={"file": ["No file was submitted."]}),
                    OpenApiExample("Empty file", value={"file": ["The submitted file is empty."]}),
                ],
            ),
            401: UNAUTHORIZED,
            403: FORBIDDEN,
            413: OpenApiResponse(response=OpenApiTypes.OBJECT, examples=[OpenApiExample("Too large", value={"error": PICTURE_TOO_LARGE_MESSAGE})]),
            503: OpenApiResponse(response=OpenApiTypes.OBJECT, examples=[OpenApiExample("Storage down", value={"error": STORAGE_UNAVAILABLE_MESSAGE})]),
        },
        methods=["PUT"],
    )
    @extend_schema(
        tags=["My account"],
        summary="Remove my profile picture",
        description=(
            "Removes the logged-in user's picture and deletes it from storage. 204 with no body, also when there was "
            "none. Afterwards `profile_picture` is `null` everywhere, so show the initials avatar. Recorded in the "
            "audit log.\n\n" + AUTH_NOTE
        ),
        responses={204: OpenApiResponse(description="Removed."), 401: UNAUTHORIZED, 403: FORBIDDEN},
        methods=["DELETE"],
    )
    @action(detail=False, methods=["put", "delete"], parser_classes=[MultiPartParser, FormParser])
    def picture(self, request):
        user = request.user
        old = user.profile_picture_public_id
        if request.method == "DELETE":
            if old:
                with transaction.atomic():
                    user.profile_picture_url, user.profile_picture_public_id = "", ""
                    user.save(update_fields=["profile_picture_url", "profile_picture_public_id"])
                    AuditLog.record(AuditLog.PROFILE_PICTURE_REMOVED, actor=user, target=user, request=request)
                file_storage.delete(old, image=True)
            return Response(status=status.HTTP_204_NO_CONTENT)

        serializer = PictureUploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        upload = serializer.validated_data["file"]
        if upload.size > MAX_PICTURE_BYTES:
            return Response({"error": PICTURE_TOO_LARGE_MESSAGE}, status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)
        data = upload.read()
        if picture_format(data) not in PICTURE_FORMATS:
            raise serializers.ValidationError({"file": [PICTURE_TYPE_MESSAGE]})
        try:
            # A random part per upload: the URL can't be guessed and changes every time (no stale browser cache).
            stored = file_storage.store_public_image(data, f"axiomvault/avatars/{user.id}/{secrets.token_hex(8)}")
        except StorageUnavailable:
            return Response({"error": STORAGE_UNAVAILABLE_MESSAGE}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        try:
            with transaction.atomic():
                user.profile_picture_url, user.profile_picture_public_id = stored["url"], stored["public_id"]
                user.save(update_fields=["profile_picture_url", "profile_picture_public_id"])
                AuditLog.record(AuditLog.PROFILE_PICTURE_UPDATED, actor=user, target=user, request=request, details={"replaced": bool(old)})
        except Exception:
            file_storage.delete(stored["public_id"], image=True)
            raise
        if old:
            file_storage.delete(old, image=True)
        return Response({"profile_picture": stored["url"]})
