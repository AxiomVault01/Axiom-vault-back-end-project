"""Admin Control Panel endpoints, for AxiomVault staff only (features 19-27)."""

import uuid

from django.db import transaction
from django.db.models import Count, Max, Q
from django.http import Http404
from django.utils import timezone
from drf_spectacular.utils import OpenApiParameter
from rest_framework.decorators import action

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiExample, OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers, status, viewsets
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

from .admin_serializers import (
    OrganisationDetailSerializer,
    OrganisationFilterSerializer,
    OrganisationRowSerializer,
    OrganisationWriteSerializer,
    SuspendSerializer,
)
from .models import AuditLog, Organisation, User
from .permissions import IsAxiomVaultStaff, PasswordChangeNotRequired
from .serializers import AdminLoginSerializer, LogoutSerializer
from .services import (
    INVALID_CREDENTIALS_MESSAGE,
    LOGOUT_SUCCESS_MESSAGE,
    LOGOUT_TOKEN_INVALID_MESSAGE,
    LOGOUT_WRONG_ACCOUNT_MESSAGE,
    NOT_ADMIN_MESSAGE,
    ORGANISATION_TAKEN_MESSAGE,
    AuthService,
    blacklist_all_refresh_tokens,
)

ADMIN_TAG = "Admin Control Panel"

AdminLoginResponse = inline_serializer(
    name="AdminLoginResponse",
    fields={
        "access": serializers.CharField(help_text="Access token, valid 30 minutes. Send it as `Authorization: Bearer <access>`."),
        "refresh": serializers.CharField(help_text="Refresh token, valid 1 day. Use it with `POST /api/v1/auth/token/refresh/`."),
        "user": inline_serializer(
            name="AdminUser",
            fields={
                "id": serializers.UUIDField(help_text="The staff member's ID."),
                "email": serializers.EmailField(help_text="Login email."),
                "full_name": serializers.CharField(help_text="Name shown in the panel; may be empty for accounts made by `createsuperuser`."),
                "admin_role": serializers.ChoiceField(
                    choices=["owner", "staff"],
                    help_text="`owner`: the platform owner, who can also create and manage staff accounts. "
                    "`staff`: everything else in the panel, but not staff management.",
                ),
                "must_change_password": serializers.BooleanField(
                    help_text="`true` for a staff member still on the temporary password the owner gave them. Show the "
                    "change-password screen (`POST /api/v1/auth/change-password/`); other endpoints answer 403 until then."
                ),
                "profile_picture": serializers.URLField(allow_null=True, help_text="Public URL of their picture, or `null`."),
            },
        ),
    },
)


class AdminAuthViewSet(viewsets.ViewSet):
    """Sign-in for the Admin Control Panel."""

    permission_classes = [AllowAny]
    authentication_classes = []  # A stale token in the header must not block signing in.

    @extend_schema(
        tags=[ADMIN_TAG],
        summary="Admin Control Panel sign in",
        description=(
            "**Admin Control Panel sign-in screen.** Only AxiomVault staff can sign in here. Cooperative accounts "
            "(super admins and team members) use `POST /api/v1/auth/login/` instead and are refused here, even with "
            "the right password.\n\n"
            "**Request fields**\n"
            "- `email` (required): the staff account's email, any letter case\n"
            "- `password` (required)\n\n"
            "**200** returns an `access` token (30 minutes), a `refresh` token (1 day), and the `user`. "
            "`user.admin_role` decides what the panel shows:\n"
            "- `owner`: the platform owner (one person). Sees everything, **including staff management**.\n"
            "- `staff`: support and operations. Sees everything except staff management.\n\n"
            "If `user.must_change_password` is `true`, the owner created this account with a temporary password: "
            "show the change-password screen first.\n\n"
            "**After signing in:** keep the session alive with `POST /api/v1/auth/token/refresh/` and sign out with "
            "`POST /api/v1/auth/logout/`, the same endpoints the app uses.\n\n"
            "**Errors**\n"
            "- `401`: wrong email or password, or the account is deactivated. Same message either way.\n"
            "- `403` with `code: \"not_admin\"`: the password is right but this is not a staff account. Tell the "
            "person to use the Cooperative app instead.\n"
            "- `400`: a field is missing or the email is malformed.\n\n"
            "**Authentication:** none; this is the sign-in."
        ),
        request=AdminLoginSerializer,
        examples=[OpenApiExample("Sign in", value={"email": "owner@axiomvault.ng", "password": "Str0ng-Passw0rd!"}, request_only=True)],
        responses={
            200: OpenApiResponse(
                response=AdminLoginResponse,
                examples=[
                    OpenApiExample(
                        "Owner",
                        value={
                            "access": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
                            "refresh": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
                            "user": {
                                "id": "4b1f0c2e-7d3a-4e5f-9a8b-1c2d3e4f5a6b", "email": "owner@axiomvault.ng",
                                "full_name": "Platform Owner", "admin_role": "owner", "must_change_password": False,
                                "profile_picture": None,
                            },
                        },
                    ),
                ],
            ),
            400: OpenApiResponse(response=OpenApiTypes.OBJECT, examples=[OpenApiExample("Missing password", value={"password": ["This field is required."]})]),
            401: OpenApiResponse(response=OpenApiTypes.OBJECT, examples=[OpenApiExample("Wrong credentials", value={"error": INVALID_CREDENTIALS_MESSAGE})]),
            403: OpenApiResponse(response=OpenApiTypes.OBJECT, examples=[OpenApiExample("Not staff", value={"error": NOT_ADMIN_MESSAGE, "code": "not_admin"})]),
        },
        auth=[],
    )
    def login(self, request):
        serializer = AdminLoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        result = AuthService.admin_login(request, serializer.validated_data["email"], serializer.validated_data["password"])
        user = User.objects.get(pk=result["user"]["id"])
        AuditLog.record(AuditLog.LOGIN, actor=user, details={"panel": "admin", "admin_role": result["user"]["admin_role"]}, request=request)
        return Response(result, status=status.HTTP_200_OK)


class AdminLogoutViewSet(viewsets.ViewSet):
    """Sign-out for the Admin Control Panel."""

    # No temporary-password block: a staff member who has not changed it yet can still sign out.
    permission_classes = [IsAuthenticated, IsAxiomVaultStaff]

    @extend_schema(
        tags=[ADMIN_TAG],
        summary="Admin Control Panel sign out",
        description=(
            "**Sign out button** in the Admin Control Panel. Ends this session: the `refresh` token is blacklisted, "
            "so `POST /api/v1/auth/token/refresh/` refuses it from now on. The current access token still works until "
            "it expires (at most 30 minutes), so the panel should also delete both tokens from memory.\n\n"
            "**Request fields**\n"
            "- `refresh` (required): the refresh token from the admin sign-in\n\n"
            "**Responses**\n"
            "- `200`: signed out; go to the sign-in screen\n"
            "- `400`: `refresh` missing, already used for sign-out, expired, or not a refresh token\n"
            "- `401`: no access token, or it is invalid or expired; just clear the stored tokens\n"
            "- `403` with `code: \"not_admin\"`: the access token belongs to a Cooperative account, not staff\n"
            "- `403` (no code): the refresh token belongs to a different account\n\n"
            "Works for a staff member who still has a temporary password. Recorded in the audit log.\n\n"
            "**Authentication:** `Authorization: Bearer <access_token>` of a staff or owner account."
        ),
        request=LogoutSerializer,
        examples=[OpenApiExample("Sign out", value={"refresh": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."}, request_only=True)],
        responses={
            200: OpenApiResponse(response=OpenApiTypes.OBJECT, examples=[OpenApiExample("Signed out", value={"message": LOGOUT_SUCCESS_MESSAGE})]),
            400: OpenApiResponse(
                response=OpenApiTypes.OBJECT,
                examples=[
                    OpenApiExample("Already signed out", value={"error": LOGOUT_TOKEN_INVALID_MESSAGE}),
                    OpenApiExample("Missing refresh", value={"refresh": ["This field is required."]}),
                ],
            ),
            401: OpenApiResponse(response=OpenApiTypes.OBJECT, examples=[OpenApiExample("No token", value={"detail": "Authentication credentials were not provided."})]),
            403: OpenApiResponse(
                response=OpenApiTypes.OBJECT,
                examples=[
                    OpenApiExample("Not staff", value={"error": NOT_ADMIN_MESSAGE, "code": "not_admin"}),
                    OpenApiExample("Someone else's token", value={"error": LOGOUT_WRONG_ACCOUNT_MESSAGE}),
                ],
            ),
        },
    )
    def logout(self, request):
        serializer = LogoutSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        AuthService.logout(request.user, serializer.validated_data["refresh"])
        AuditLog.record(AuditLog.LOGOUT, actor=request.user, details={"panel": "admin"}, request=request)
        return Response({"message": LOGOUT_SUCCESS_MESSAGE}, status=status.HTTP_200_OK)


# ---- 20: organisation management (ACP-01) ----

ORG_EXAMPLE = {
    "id": "b3f1c2d4-5e6f-4a7b-8c9d-0e1f2a3b4c5d",
    "name": "Ikeja Cooperative",
    "status": "active",
    "is_demo": False,
    "created_at": "2026-10-01T09:00:00Z",
    "suspended_at": None,
    "suspension_reason": "",
    "super_admin": {"id": "2d089ad3-9545-4611-a9dc-731c3a81879f", "full_name": "Ngozi Okafor", "email": "ngozi@ikejacoop.ng"},
    "members_count": 5,
    "active_members_count": 4,
}
ORG_DETAIL_EXAMPLE = {**ORG_EXAMPLE, "alerts_count": 12, "open_alerts_count": 7, "cases_count": 3, "uploads_count": 9, "last_upload_at": "2026-10-20T14:02:00Z"}
SUSPENDED_EXAMPLE = {**ORG_DETAIL_EXAMPLE, "status": "suspended", "suspended_at": "2026-10-22T10:00:00Z", "suspension_reason": "Unpaid invoice INV-2026-014."}
ADMIN_AUTH_NOTE = (
    "**Authentication:** `Authorization: Bearer <access_token>` from the admin sign-in "
    "(`POST /api/v1/admin/auth/login/`). Owner and staff can both use it. A Cooperative account's token gets 403 "
    "`not_admin`."
)
ORG_ID_PARAM = OpenApiParameter("id", OpenApiTypes.UUID, OpenApiParameter.PATH, description="The organisation's ID.")
ADMIN_401 = OpenApiResponse(response=OpenApiTypes.OBJECT, examples=[OpenApiExample("No token", value={"detail": "Authentication credentials were not provided."})])
ADMIN_403 = OpenApiResponse(response=OpenApiTypes.OBJECT, examples=[OpenApiExample("Not staff", value={"error": NOT_ADMIN_MESSAGE, "code": "not_admin"})])
ORG_404 = OpenApiResponse(response=OpenApiTypes.OBJECT, examples=[OpenApiExample("Not found", value={"detail": "No organisation with this ID."})])
ALREADY_SUSPENDED = "This organisation is already suspended."
ALREADY_ACTIVE = "This organisation is already active."
OPEN_ALERT_STATUSES = ["new", "investigating", "escalated"]


def _paged(results):
    return {"count": len(results), "next": None, "previous": None, "results": results}


class OrganisationAdminViewSet(viewsets.GenericViewSet):
    """Admin Control Panel: Organisations screen."""

    permission_classes = [IsAuthenticated, PasswordChangeNotRequired, IsAxiomVaultStaff]
    serializer_class = OrganisationRowSerializer
    queryset = Organisation.objects.none()  # Only tells Swagger the model; requests use get_queryset().

    def get_queryset(self):
        return (
            Organisation.objects.annotate(
                members_count=Count("users", distinct=True),
                active_members_count=Count("users", filter=Q(users__is_active=True), distinct=True),
            )
            .prefetch_related("users")
            .order_by("-created_at")
        )

    def get_object(self):
        try:
            organisation = self.get_queryset().filter(pk=uuid.UUID(str(self.kwargs["pk"]))).first()
        except ValueError:
            organisation = None
        if organisation is None:
            raise Http404("No organisation with this ID.")
        return organisation

    def _detail(self, organisation, code=status.HTTP_200_OK):
        from fraud.models import Alert, Case
        from ingestion.models import DataUpload

        uploads = DataUpload.objects.filter(organisation=organisation)
        alerts = Alert.objects.filter(organisation=organisation)
        organisation.alerts_count = alerts.count()
        organisation.open_alerts_count = alerts.filter(status__in=OPEN_ALERT_STATUSES).count()
        organisation.cases_count = Case.objects.filter(organisation=organisation).count()
        organisation.uploads_count = uploads.count()
        organisation.last_upload_at = uploads.aggregate(last=Max("created_at"))["last"]
        return Response(OrganisationDetailSerializer(organisation).data, status=code)

    def _fresh(self, organisation):
        return self.get_queryset().get(pk=organisation.pk)

    @extend_schema(
        tags=[ADMIN_TAG],
        summary="List organisations",
        description=(
            "**Organisations screen** of the Admin Control Panel: every Cooperative on AxiomVault, newest first, with "
            "status, super admin, and member counts. Demo organisations are marked `is_demo` (removed before launch).\n\n"
            "**Filters** (optional): `status` (`active` or `suspended`; anything else is 400) and `search` (part of "
            "the name, letter case ignored).\n\n"
            "**Paginated:** 20 a page; `?page=2`, `?page_size=` up to 100; `{count, next, previous, results}`.\n\n"
            + ADMIN_AUTH_NOTE
        ),
        parameters=[
            OpenApiParameter("status", OpenApiTypes.STR, description="`active` or `suspended`."),
            OpenApiParameter("search", OpenApiTypes.STR, description="Part of the name."),
            OpenApiParameter("page", OpenApiTypes.INT, description="Page number, from 1."),
            OpenApiParameter("page_size", OpenApiTypes.INT, description="Items per page, default 20, at most 100."),
        ],
        responses={
            200: OpenApiResponse(response=OrganisationRowSerializer(many=True), examples=[OpenApiExample("Organisations", value=_paged([ORG_EXAMPLE]))]),
            400: OpenApiResponse(response=OpenApiTypes.OBJECT, examples=[OpenApiExample("Unknown status", value={"status": ['"closed" is not a valid choice.']})]),
            401: ADMIN_401,
            403: ADMIN_403,
        },
    )
    def list(self, request):
        filters = OrganisationFilterSerializer(data=request.query_params)
        filters.is_valid(raise_exception=True)
        organisations = self.get_queryset()
        if "status" in filters.validated_data:
            organisations = organisations.filter(status=filters.validated_data["status"])
        if filters.validated_data.get("search", "").strip():
            organisations = organisations.filter(name__icontains=filters.validated_data["search"].strip())
        page = self.paginate_queryset(organisations)
        return self.get_paginated_response(OrganisationRowSerializer(page, many=True).data)

    @extend_schema(
        tags=[ADMIN_TAG],
        summary="Create an organisation",
        description=(
            "**Add Organisation** in the Admin Control Panel. Creates an active Cooperative with no users yet; its "
            "super admin is added from the Users screen (coming with feature 21). Cooperatives that sign up "
            "themselves get their organisation automatically.\n\n"
            "**Fields:** `name` (required, 1-255, spaces trimmed, unique ignoring letter case).\n\n"
            "201 returns the organisation detail. Recorded in the audit log.\n\n" + ADMIN_AUTH_NOTE
        ),
        request=OrganisationWriteSerializer,
        examples=[OpenApiExample("Create", value={"name": "Lekki Cooperative"}, request_only=True)],
        responses={
            201: OpenApiResponse(
                response=OrganisationDetailSerializer,
                examples=[OpenApiExample("Created", value={
                    **ORG_DETAIL_EXAMPLE, "name": "Lekki Cooperative", "super_admin": None, "members_count": 0,
                    "active_members_count": 0, "alerts_count": 0, "open_alerts_count": 0, "cases_count": 0,
                    "uploads_count": 0, "last_upload_at": None,
                })],
            ),
            400: OpenApiResponse(
                response=OpenApiTypes.OBJECT,
                examples=[
                    OpenApiExample("Name taken", value={"name": [ORGANISATION_TAKEN_MESSAGE]}),
                    OpenApiExample("Blank", value={"name": ["This field may not be blank."]}),
                ],
            ),
            401: ADMIN_401,
            403: ADMIN_403,
        },
    )
    def create(self, request):
        serializer = OrganisationWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            organisation = Organisation.objects.create(name=serializer.validated_data["name"])
            AuditLog.record(
                AuditLog.ORGANISATION_CREATED, actor=request.user, target=organisation, organisation=organisation,
                request=request, details={"name": organisation.name},
            )
        return self._detail(self._fresh(organisation), status.HTTP_201_CREATED)

    @extend_schema(
        tags=[ADMIN_TAG],
        summary="View an organisation",
        description=(
            "**Organisation detail page.** Everything in the list row plus activity counts: alerts (and open ones), "
            "cases, uploads, and the latest upload time.\n\n" + ADMIN_AUTH_NOTE
        ),
        parameters=[ORG_ID_PARAM],
        responses={200: OpenApiResponse(response=OrganisationDetailSerializer, examples=[OpenApiExample("Organisation", value=ORG_DETAIL_EXAMPLE)]), 401: ADMIN_401, 403: ADMIN_403, 404: ORG_404},
    )
    def retrieve(self, request, pk=None):
        return self._detail(self.get_object())

    @extend_schema(
        tags=[ADMIN_TAG],
        summary="Rename an organisation",
        description=(
            "**Edit Organisation.** Changes the name (`name`, same rules as create). Status changes use the suspend "
            "and reactivate endpoints. 200 returns the detail. Recorded in the audit log with the old and new name.\n\n"
            + ADMIN_AUTH_NOTE
        ),
        parameters=[ORG_ID_PARAM],
        request=OrganisationWriteSerializer,
        examples=[OpenApiExample("Rename", value={"name": "Ikeja Multipurpose Cooperative"}, request_only=True)],
        responses={
            200: OpenApiResponse(response=OrganisationDetailSerializer, examples=[OpenApiExample("Renamed", value={**ORG_DETAIL_EXAMPLE, "name": "Ikeja Multipurpose Cooperative"})]),
            400: OpenApiResponse(response=OpenApiTypes.OBJECT, examples=[OpenApiExample("Name taken", value={"name": [ORGANISATION_TAKEN_MESSAGE]})]),
            401: ADMIN_401, 403: ADMIN_403, 404: ORG_404,
        },
    )
    def partial_update(self, request, pk=None):
        organisation = self.get_object()
        serializer = OrganisationWriteSerializer(organisation, data=request.data)
        serializer.is_valid(raise_exception=True)
        old_name, new_name = organisation.name, serializer.validated_data["name"]
        if new_name != old_name:
            with transaction.atomic():
                Organisation.objects.filter(pk=organisation.pk).update(name=new_name)
                AuditLog.record(
                    AuditLog.ORGANISATION_UPDATED, actor=request.user, target=organisation, organisation=organisation,
                    request=request, details={"from": old_name, "to": new_name},
                )
        return self._detail(self._fresh(organisation))

    @extend_schema(
        tags=[ADMIN_TAG],
        summary="Suspend an organisation",
        description=(
            "**Suspend** on the organisation page. From now on its users can't log in (403 "
            "`organisation_suspended`) or use the API, and **all of them are signed out at once** (their refresh "
            "tokens stop working; any access token in use is refused on its next request). Their data is kept.\n\n"
            "**Fields:** `reason` (required, 1-500), shown to staff only.\n\n"
            "200 returns the detail with `status` set to `suspended`. Suspending a suspended organisation is 400. "
            "Recorded in the audit log with the reason.\n\n" + ADMIN_AUTH_NOTE
        ),
        parameters=[ORG_ID_PARAM],
        request=SuspendSerializer,
        examples=[OpenApiExample("Suspend", value={"reason": "Unpaid invoice INV-2026-014."}, request_only=True)],
        responses={
            200: OpenApiResponse(response=OrganisationDetailSerializer, examples=[OpenApiExample("Suspended", value=SUSPENDED_EXAMPLE)]),
            400: OpenApiResponse(
                response=OpenApiTypes.OBJECT,
                examples=[
                    OpenApiExample("Already suspended", value={"status": [ALREADY_SUSPENDED]}),
                    OpenApiExample("No reason", value={"reason": ["This field is required."]}),
                ],
            ),
            401: ADMIN_401, 403: ADMIN_403, 404: ORG_404,
        },
    )
    @action(detail=True, methods=["post"])
    def suspend(self, request, pk=None):
        organisation = self.get_object()
        serializer = SuspendSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        if organisation.is_suspended:
            raise serializers.ValidationError({"status": [ALREADY_SUSPENDED]})
        reason = serializer.validated_data["reason"].strip()
        with transaction.atomic():
            Organisation.objects.filter(pk=organisation.pk).update(
                status=Organisation.SUSPENDED, suspended_at=timezone.now(), suspension_reason=reason
            )
            users = list(organisation.users.all())
            for user in users:
                blacklist_all_refresh_tokens(user)
            AuditLog.record(
                AuditLog.ORGANISATION_SUSPENDED, actor=request.user, target=organisation, organisation=organisation,
                request=request, details={"reason": reason, "users_signed_out": len(users)},
            )
        return self._detail(self._fresh(organisation))

    @extend_schema(
        tags=[ADMIN_TAG],
        summary="Reactivate an organisation",
        description=(
            "**Reactivate** a suspended organisation. Its users can log in again (they sign in afresh; old sessions "
            "stay ended). No body. 200 returns the detail with `status` set to `active`. Reactivating an active "
            "organisation is 400. Recorded in the audit log.\n\n" + ADMIN_AUTH_NOTE
        ),
        parameters=[ORG_ID_PARAM],
        request=None,
        responses={
            200: OpenApiResponse(response=OrganisationDetailSerializer, examples=[OpenApiExample("Reactivated", value=ORG_DETAIL_EXAMPLE)]),
            400: OpenApiResponse(response=OpenApiTypes.OBJECT, examples=[OpenApiExample("Already active", value={"status": [ALREADY_ACTIVE]})]),
            401: ADMIN_401, 403: ADMIN_403, 404: ORG_404,
        },
    )
    @action(detail=True, methods=["post"])
    def reactivate(self, request, pk=None):
        organisation = self.get_object()
        if not organisation.is_suspended:
            raise serializers.ValidationError({"status": [ALREADY_ACTIVE]})
        with transaction.atomic():
            Organisation.objects.filter(pk=organisation.pk).update(status=Organisation.ACTIVE, suspended_at=None, suspension_reason="")
            AuditLog.record(
                AuditLog.ORGANISATION_REACTIVATED, actor=request.user, target=organisation, organisation=organisation,
                request=request, details={"previous_reason": organisation.suspension_reason},
            )
        return self._detail(self._fresh(organisation))
