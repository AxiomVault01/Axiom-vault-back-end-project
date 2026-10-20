"""The logged-in user's own account: profile picture (feature 32); the profile itself comes with feature 17."""

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

from .models import AuditLog
from .permissions import PASSWORD_CHANGE_REQUIRED_MESSAGE

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


class MeViewSet(viewsets.ViewSet):
    """Endpoints about the caller's own account."""

    parser_classes = [MultiPartParser, FormParser]

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
    @action(detail=False, methods=["put", "delete"])
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
