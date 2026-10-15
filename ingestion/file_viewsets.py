import logging
import os
import uuid
from datetime import datetime, timezone

from django.db import transaction
from django.http import Http404
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import serializers, status, viewsets
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from accounts.models import AuditLog
from accounts.permissions import PASSWORD_CHANGE_REQUIRED_MESSAGE, PasswordChangeNotRequired
from config import file_storage
from config.file_storage import StorageUnavailable

from .models import StoredFile
from .viewsets import FILE_TOO_LARGE_MESSAGE, MAX_FILE_BYTES, NO_ORGANISATION_MESSAGE, STORAGE_UNAVAILABLE_MESSAGE, BelongsToOrganisation

logger = logging.getLogger(__name__)
DEFAULT_CONTENT_TYPE = "application/octet-stream"


class AnyFileSerializer(serializers.Serializer):
    file = serializers.FileField(help_text="Any file type, at most 10 MB. Must not be empty.", label="File")


class UploaderSerializer(serializers.Serializer):
    id = serializers.UUIDField(help_text="The uploader's user ID.")
    full_name = serializers.CharField(help_text="The uploader's name.")


class StoredFileSerializer(serializers.Serializer):
    id = serializers.UUIDField(help_text="The file's ID. Keep it to attach the file (e.g. as case evidence) or read it again.")
    file_name = serializers.CharField(help_text="The name the file was uploaded with.")
    content_type = serializers.CharField(help_text="Type the browser sent, e.g. `application/pdf`; `application/octet-stream` when none.")
    size = serializers.IntegerField(help_text="Size in bytes.")
    uploaded_by = UploaderSerializer(allow_null=True, help_text="Who uploaded it, or `null` if that account was removed.")
    created_at = serializers.DateTimeField(help_text="When it was uploaded (ISO 8601).")
    url = serializers.URLField(help_text="Signed link that downloads the file. Works for 1 hour; read the file again for a new one.")
    url_expires_at = serializers.DateTimeField(help_text="When `url` stops working (ISO 8601).")


def describe(stored):
    """The API shape of a stored file, with a fresh download link."""
    url, expires_at = file_storage.download_url(stored.public_id, stored.file_name)
    uploader = stored.uploaded_by
    return {
        "id": str(stored.id),
        "file_name": stored.file_name,
        "content_type": stored.content_type,
        "size": stored.size,
        "uploaded_by": {"id": str(uploader.id), "full_name": uploader.full_name} if uploader else None,
        "created_at": stored.created_at,
        "url": url,
        "url_expires_at": datetime.fromtimestamp(expires_at, tz=timezone.utc),
    }


FILE_EXAMPLE = {
    "id": "3f2b9c1e-8a4d-4e6f-9b2c-7d1e5a8f0c3b",
    "file_name": "gtbank-statement-oct.pdf",
    "content_type": "application/pdf",
    "size": 248133,
    "uploaded_by": {"id": "2d089ad3-9545-4611-a9dc-731c3a81879f", "full_name": "Ngozi Okafor"},
    "created_at": "2026-10-15T10:00:00Z",
    "url": "https://api.cloudinary.com/v1_1/<cloud>/raw/download?api_key=…&attachment=1&expires_at=1792080000&signature=…",
    "url_expires_at": "2026-10-15T11:00:00Z",
}
AUTH_NOTE = (
    "**Authentication:** `Authorization: Bearer <access_token>` of any member of an organisation (super admin or "
    "team member). Files belong to the uploader's organisation; other organisations' files are 404."
)
UNAUTHORIZED = OpenApiResponse(
    response=OpenApiTypes.OBJECT,
    description="No access token, or it is expired or invalid.",
    examples=[OpenApiExample("No token", value={"detail": "Authentication credentials were not provided."})],
)
FORBIDDEN = OpenApiResponse(
    response=OpenApiTypes.OBJECT,
    description="Temporary password not changed yet, or the account belongs to no organisation (staff).",
    examples=[
        OpenApiExample("Temporary password", value={"error": PASSWORD_CHANGE_REQUIRED_MESSAGE, "code": "password_change_required"}),
        OpenApiExample("No organisation", value={"error": NO_ORGANISATION_MESSAGE}),
    ],
)
UNAVAILABLE = OpenApiResponse(
    response=OpenApiTypes.OBJECT,
    description="File storage is not set up or not reachable. Nothing was saved; try again later.",
    examples=[OpenApiExample("Storage down", value={"error": STORAGE_UNAVAILABLE_MESSAGE})],
)


class StoredFileViewSet(viewsets.GenericViewSet):
    """General file upload: any attachment, stored privately."""

    parser_classes = [MultiPartParser, FormParser]
    permission_classes = [IsAuthenticated, PasswordChangeNotRequired, BelongsToOrganisation]
    serializer_class = StoredFileSerializer
    queryset = StoredFile.objects.none()  # Only tells Swagger the model; requests use get_queryset().

    def get_queryset(self):
        return StoredFile.objects.filter(organisation=self.request.user.organisation).select_related("uploaded_by")

    @extend_schema(
        tags=["Files"],
        summary="Upload any file",
        description=(
            "**General upload route** for attachments of any type: bank statements, PDFs, images, spreadsheets, "
            "emails. Case evidence (Case detail screen) uploads here first, then attaches the returned `id` to the "
            "case.\n\n"
            "**Request:** `multipart/form-data` with one field, `file`: any type, at most **10 MB**, not empty. "
            "The file is stored privately for your organisation; nobody can open it without a signed link.\n\n"
            "**201** returns the file's `id`, name, type, size, uploader, time, and `url`: a signed link that "
            "downloads the file and **expires after 1 hour** (`url_expires_at`). Don't store the link; store the "
            "`id` and call `GET /api/v1/files/{id}/` for a fresh link when someone wants to download it.\n\n"
            "**Errors:** no `file` field or an empty file is 400; a file over 10 MB is 413; 503 means storage is "
            "down and nothing was saved (ask the user to try again in a few minutes). Every upload is recorded in "
            "the audit log.\n\n" + AUTH_NOTE
        ),
        request={"multipart/form-data": AnyFileSerializer},
        responses={
            201: OpenApiResponse(response=StoredFileSerializer, examples=[OpenApiExample("Uploaded", value=FILE_EXAMPLE)]),
            400: OpenApiResponse(
                response=OpenApiTypes.OBJECT,
                examples=[
                    OpenApiExample("No file", value={"file": ["No file was submitted."]}),
                    OpenApiExample("Empty file", value={"file": ["The submitted file is empty."]}),
                ],
            ),
            401: UNAUTHORIZED,
            403: FORBIDDEN,
            413: OpenApiResponse(response=OpenApiTypes.OBJECT, examples=[OpenApiExample("Too large", value={"error": FILE_TOO_LARGE_MESSAGE})]),
            503: UNAVAILABLE,
        },
    )
    def create(self, request):
        serializer = AnyFileSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        upload = serializer.validated_data["file"]
        if upload.size > MAX_FILE_BYTES:
            return Response({"error": FILE_TOO_LARGE_MESSAGE}, status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)

        file_id = uuid.uuid4()
        file_name = os.path.basename(upload.name or "file")[:255]
        extension = os.path.splitext(file_name)[1].lower()[:10]
        try:
            stored = file_storage.store_private(upload.read(), f"axiomvault/{request.user.organisation_id}/files/{file_id}{extension}")
        except StorageUnavailable:
            return Response({"error": STORAGE_UNAVAILABLE_MESSAGE}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        try:
            with transaction.atomic():
                record = StoredFile.objects.create(
                    id=file_id, organisation=request.user.organisation, uploaded_by=request.user, file_name=file_name,
                    content_type=(upload.content_type or DEFAULT_CONTENT_TYPE)[:100], size=upload.size,
                    public_id=stored["public_id"],
                )
                AuditLog.record(
                    AuditLog.FILE_UPLOADED, actor=request.user, target=record, request=request,
                    details={"file_name": file_name, "size": upload.size},
                )
        except Exception:
            # Keep storage and database in step: no stored file without its record.
            file_storage.delete(stored["public_id"])
            raise
        try:
            body = describe(record)
        except StorageUnavailable:
            return Response({"error": STORAGE_UNAVAILABLE_MESSAGE}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        return Response(body, status=status.HTTP_201_CREATED)

    @extend_schema(
        tags=["Files"],
        summary="Read a file and get a fresh download link",
        description=(
            "Returns the file's details and a new signed `url` that downloads it, valid for 1 hour "
            "(`url_expires_at`). Call this whenever someone clicks Download, instead of keeping old links.\n\n"
            "Only files of your own organisation exist for you: another organisation's ID, an unknown ID, or a "
            "malformed ID is 404. 503 means storage is not reachable right now.\n\n" + AUTH_NOTE
        ),
        parameters=[OpenApiParameter("id", OpenApiTypes.UUID, OpenApiParameter.PATH, description="The file's ID from the upload response.")],
        responses={
            200: OpenApiResponse(response=StoredFileSerializer, examples=[OpenApiExample("File", value=FILE_EXAMPLE)]),
            401: UNAUTHORIZED,
            403: FORBIDDEN,
            404: OpenApiResponse(response=OpenApiTypes.OBJECT, examples=[OpenApiExample("Not found", value={"detail": "No file with this ID."})]),
            503: UNAVAILABLE,
        },
    )
    def retrieve(self, request, pk=None):
        try:
            stored = self.get_queryset().filter(pk=uuid.UUID(str(pk))).first()
        except ValueError:
            stored = None
        if stored is None:
            raise Http404("No file with this ID.")
        try:
            return Response(describe(stored))
        except StorageUnavailable:
            return Response({"error": STORAGE_UNAVAILABLE_MESSAGE}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
