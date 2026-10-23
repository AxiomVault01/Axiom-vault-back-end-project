"""Admin Control Panel endpoints, for AxiomVault staff only (features 19-27)."""

import uuid

from django.db import IntegrityError, transaction
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
    AdminUserCreateSerializer,
    AdminUserFilterSerializer,
    AdminUserSerializer,
    AdminUserUpdateSerializer,
    OrganisationDetailSerializer,
    OrganisationFilterSerializer,
    OrganisationRowSerializer,
    OrganisationWriteSerializer,
    StaffCreateSerializer,
    StaffSerializer,
    StaffUpdateSerializer,
    SuspendSerializer,
)
from .models import AuditLog, Organisation, User
from .permissions import OWNER_ONLY_MESSAGE, IsAxiomVaultStaff, IsPlatformOwner, PasswordChangeNotRequired
from .serializers import EMAIL_TAKEN_MESSAGE
from .throttling import LOGIN_LIMIT, AuthRateLimitMixin, rate_limited
from .serializers import AdminLoginSerializer, LogoutSerializer
from .services import (
    INVALID_CREDENTIALS_MESSAGE,
    LOGOUT_SUCCESS_MESSAGE,
    LOGOUT_TOKEN_INVALID_MESSAGE,
    LOGOUT_WRONG_ACCOUNT_MESSAGE,
    NOT_ADMIN_MESSAGE,
    DEACTIVATED_RESET_MESSAGE,
    ORGANISATION_TAKEN_MESSAGE,
    SUPER_ADMIN_EXISTS_MESSAGE,
    AdminAccountService,
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
            name="AdminSignInUser",
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


class AdminAuthViewSet(AuthRateLimitMixin, viewsets.ViewSet):
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
            "- `400`: a field is missing or the email is malformed.\n"
            "- `429`: rate limit. At most 10 sign-in attempts a minute from one IP address, and 10 every 15 minutes "
            "for one email, counted together with the app's `POST /api/v1/auth/login/`. Wait `retry_after` seconds.\n\n"
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
            429: rate_limited(LOGIN_LIMIT),
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


# ---- 21: users and staff (ACP-02) ----

USER_EXAMPLE = {
    "id": "7c1e2f4a-9b3d-4e6f-8a2c-1d5b7e9f0a3c", "email": "ada.obi@ikejacoop.ng", "full_name": "Ada Obi",
    "account_type": "team_member", "role": "Payroll Analyst", "phone_number": "+2348031234567",
    "organisation": {"id": "b3f1c2d4-5e6f-4a7b-8c9d-0e1f2a3b4c5d", "name": "Ikeja Cooperative", "status": "active"},
    "is_active": True, "must_change_password": False, "profile_picture": None,
    "date_joined": "2026-10-05T09:00:00Z", "last_login": "2026-10-21T08:30:00Z",
}
STAFF_EXAMPLE = {
    "id": "5a4b3c2d-1e0f-4a9b-8c7d-6e5f4a3b2c1d", "email": "kemi@axiomvault.ng", "full_name": "Kemi Support",
    "admin_role": "staff", "is_active": True, "must_change_password": True, "profile_picture": None,
    "date_joined": "2026-10-22T09:00:00Z", "last_login": None,
}
OWNER_ONLY_RESPONSE = OpenApiResponse(
    response=OpenApiTypes.OBJECT,
    examples=[
        OpenApiExample("Not staff", value={"error": NOT_ADMIN_MESSAGE, "code": "not_admin"}),
        OpenApiExample("Staff, not owner", value={"error": OWNER_ONLY_MESSAGE, "code": "owner_only"}),
    ],
)
ACCOUNT_ID_PARAM = OpenApiParameter("id", OpenApiTypes.UUID, OpenApiParameter.PATH, description="The account's ID.")
ACCOUNT_404 = OpenApiResponse(response=OpenApiTypes.OBJECT, examples=[OpenApiExample("Not found", value={"detail": "No account with this ID."})])
RESET_SENT_MESSAGE = "A password reset code has been emailed to {email}."


class _AccountAdminViewSet(viewsets.GenericViewSet):
    """Shared actions for the Users and Staff screens. Subclasses set `kind`, the queryset, and serializers."""

    kind = "user"
    row_serializer = AdminUserSerializer
    update_serializer = AdminUserUpdateSerializer
    queryset = User.objects.none()  # Only tells Swagger the model; requests use get_queryset().

    def get_object(self):
        try:
            account = self.get_queryset().filter(pk=uuid.UUID(str(self.kwargs["pk"]))).first()
        except ValueError:
            account = None
        if account is None:
            raise Http404("No account with this ID.")
        return account

    def _audit(self, action, account, request, **details):
        AuditLog.record(
            action, actor=request.user, target=account, organisation=account.organisation, request=request,
            details={"kind": self.kind, "email": account.email, **details},
        )

    def _retrieve(self, request):
        return Response(self.row_serializer(self.get_object()).data)

    def _update(self, request):
        account = self.get_object()
        serializer = self.update_serializer(account, data=request.data)
        serializer.is_valid(raise_exception=True)
        changed = {k: v for k, v in serializer.validated_data.items() if getattr(account, k) != (v.strip() if isinstance(v, str) else v)}
        if changed:
            with transaction.atomic():
                for name, value in changed.items():
                    setattr(account, name, value.strip() if isinstance(value, str) else value)
                try:
                    account.save(update_fields=list(changed))
                except IntegrityError:
                    raise serializers.ValidationError({"email": [EMAIL_TAKEN_MESSAGE]})
                self._audit(AuditLog.USER_UPDATED, account, request, fields=sorted(changed))
        return Response(self.row_serializer(account).data)

    def _set_active(self, request, active):
        account = self.get_object()
        if account.pk == request.user.pk:
            raise serializers.ValidationError({"error": "You can't change your own account here."})
        if account.is_active != active:
            with transaction.atomic():
                account.is_active = active
                account.save(update_fields=["is_active"])
                if not active:
                    blacklist_all_refresh_tokens(account)
                self._audit(AuditLog.USER_REACTIVATED if active else AuditLog.USER_DEACTIVATED, account, request)
        return Response(self.row_serializer(account).data)

    def _reset_password(self, request):
        account = self.get_object()
        AdminAccountService.send_password_reset(account)
        self._audit(AuditLog.PASSWORD_RESET_SENT, account, request)
        return Response({"message": RESET_SENT_MESSAGE.format(email=account.email)})


class AdminUserViewSet(_AccountAdminViewSet):
    """Admin Control Panel: Users screen (Cooperative accounts in every organisation)."""

    permission_classes = [IsAuthenticated, PasswordChangeNotRequired, IsAxiomVaultStaff]
    serializer_class = AdminUserSerializer

    def get_queryset(self):
        # Only Cooperative accounts; staff and the owner are managed on the Staff screen.
        return User.objects.filter(organisation__isnull=False, is_staff=False).select_related("organisation").order_by("-date_joined")

    @extend_schema(
        tags=[ADMIN_TAG],
        summary="List users",
        description=(
            "**Users screen** of the Admin Control Panel: Cooperative accounts (super admins and team members) in "
            "every organisation, newest first. AxiomVault staff accounts are on the Staff screen instead.\n\n"
            "**Filters** (optional, combined): `organisation` (an organisation ID), `account_type` (`super_admin` or "
            "`team_member`), `is_active` (`true` or `false`), `search` (part of the name or email). An invalid value "
            "is 400.\n\n"
            "**Paginated:** 20 a page; `?page=2`, `?page_size=` up to 100.\n\n" + ADMIN_AUTH_NOTE
        ),
        parameters=[
            OpenApiParameter("organisation", OpenApiTypes.UUID, description="Only this organisation's users."),
            OpenApiParameter("account_type", OpenApiTypes.STR, description="`super_admin` or `team_member`."),
            OpenApiParameter("is_active", OpenApiTypes.BOOL, description="`true` or `false`."),
            OpenApiParameter("search", OpenApiTypes.STR, description="Part of the name or email."),
            OpenApiParameter("page", OpenApiTypes.INT, description="Page number, from 1."),
            OpenApiParameter("page_size", OpenApiTypes.INT, description="Items per page, default 20, at most 100."),
        ],
        responses={
            200: OpenApiResponse(response=AdminUserSerializer(many=True), examples=[OpenApiExample("Users", value=_paged([USER_EXAMPLE]))]),
            400: OpenApiResponse(response=OpenApiTypes.OBJECT, examples=[OpenApiExample("Bad filter", value={"account_type": ['"staff" is not a valid choice.']})]),
            401: ADMIN_401, 403: ADMIN_403,
        },
    )
    def list(self, request):
        filters = AdminUserFilterSerializer(data=request.query_params)
        filters.is_valid(raise_exception=True)
        f = filters.validated_data
        users = self.get_queryset()
        if f.get("organisation"):
            users = users.filter(organisation_id=f["organisation"])
        if f.get("account_type"):
            users = users.filter(account_type=f["account_type"])
        if f.get("is_active") is not None:
            users = users.filter(is_active=f["is_active"])
        if f.get("search", "").strip():
            text = f["search"].strip()
            users = users.filter(Q(full_name__icontains=text) | Q(email__icontains=text))
        page = self.paginate_queryset(users)
        return self.get_paginated_response(AdminUserSerializer(page, many=True).data)

    @extend_schema(
        tags=[ADMIN_TAG],
        summary="Create a user",
        description=(
            "**Add User.** Creates a Cooperative account in any organisation, for example its first super admin "
            "when the organisation was created from the panel.\n\n"
            "**Fields:** `organisation_id`, `account_type` (`super_admin` or `team_member`; one super admin per "
            "organisation), `full_name`, `email` (unique), `password` (temporary; share it yourself, no email is "
            "sent), optional `phone_number` and `role`.\n\n"
            "The account is ready to use; the person must replace the temporary password on first login "
            "(`must_change_password: true`). 201 returns the user. Recorded in the audit log.\n\n" + ADMIN_AUTH_NOTE
        ),
        request=AdminUserCreateSerializer,
        examples=[OpenApiExample("Super admin", value={
            "organisation_id": "b3f1c2d4-5e6f-4a7b-8c9d-0e1f2a3b4c5d", "account_type": "super_admin",
            "full_name": "Ngozi Okafor", "email": "ngozi@ikejacoop.ng", "password": "Temp-Pass-2026!", "phone_number": "+2348031234567",
        }, request_only=True)],
        responses={
            201: OpenApiResponse(response=AdminUserSerializer, examples=[OpenApiExample("Created", value={**USER_EXAMPLE, "must_change_password": True, "last_login": None})]),
            400: OpenApiResponse(
                response=OpenApiTypes.OBJECT,
                examples=[
                    OpenApiExample("Email taken", value={"email": [EMAIL_TAKEN_MESSAGE]}),
                    OpenApiExample("Second super admin", value={"account_type": [SUPER_ADMIN_EXISTS_MESSAGE]}),
                    OpenApiExample("Unknown organisation", value={"organisation_id": ["No organisation with this ID."]}),
                    OpenApiExample("Weak password", value={"password": ["This password is too common."]}),
                ],
            ),
            401: ADMIN_401, 403: ADMIN_403,
        },
    )
    def create(self, request):
        serializer = AdminUserCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        organisation = Organisation.objects.filter(pk=data["organisation_id"]).first()
        if organisation is None:
            raise serializers.ValidationError({"organisation_id": ["No organisation with this ID."]})
        if data["account_type"] == User.SUPER_ADMIN and organisation.users.filter(account_type=User.SUPER_ADMIN).exists():
            raise serializers.ValidationError({"account_type": [SUPER_ADMIN_EXISTS_MESSAGE]})
        account = AdminAccountService.create(data, organisation=organisation, account_type=data["account_type"])
        self._audit(AuditLog.USER_CREATED, account, request, account_type=account.account_type)
        return Response(AdminUserSerializer(account).data, status=status.HTTP_201_CREATED)

    @extend_schema(tags=[ADMIN_TAG], summary="View a user", description="One Cooperative account.\n\n" + ADMIN_AUTH_NOTE,
                   parameters=[ACCOUNT_ID_PARAM], responses={200: OpenApiResponse(response=AdminUserSerializer, examples=[OpenApiExample("User", value=USER_EXAMPLE)]), 401: ADMIN_401, 403: ADMIN_403, 404: ACCOUNT_404})
    def retrieve(self, request, pk=None):
        return self._retrieve(request)

    @extend_schema(
        tags=[ADMIN_TAG],
        summary="Edit a user",
        description=(
            "**Edit User.** Changes only the fields sent: `full_name`, `email` (unique), `phone_number`, `role` "
            "(`\"\"` clears the last two). Account type and organisation can't be changed here. 200 returns the user. "
            "Recorded in the audit log with the changed fields.\n\n" + ADMIN_AUTH_NOTE
        ),
        parameters=[ACCOUNT_ID_PARAM],
        request=AdminUserUpdateSerializer,
        examples=[OpenApiExample("Edit", value={"role": "Senior Payroll Analyst"}, request_only=True)],
        responses={
            200: OpenApiResponse(response=AdminUserSerializer, examples=[OpenApiExample("Edited", value={**USER_EXAMPLE, "role": "Senior Payroll Analyst"})]),
            400: OpenApiResponse(response=OpenApiTypes.OBJECT, examples=[OpenApiExample("Email taken", value={"email": [EMAIL_TAKEN_MESSAGE]})]),
            401: ADMIN_401, 403: ADMIN_403, 404: ACCOUNT_404,
        },
    )
    def partial_update(self, request, pk=None):
        return self._update(request)

    @extend_schema(
        tags=[ADMIN_TAG],
        summary="Deactivate a user",
        description=(
            "**Deactivate.** The user can't log in any more and is signed out everywhere at once; their history is "
            "kept. Doing it twice is fine. 200 returns the user with `is_active: false`.\n\n" + ADMIN_AUTH_NOTE
        ),
        parameters=[ACCOUNT_ID_PARAM], request=None,
        responses={200: OpenApiResponse(response=AdminUserSerializer, examples=[OpenApiExample("Deactivated", value={**USER_EXAMPLE, "is_active": False})]), 401: ADMIN_401, 403: ADMIN_403, 404: ACCOUNT_404},
    )
    @action(detail=True, methods=["post"])
    def deactivate(self, request, pk=None):
        return self._set_active(request, False)

    @extend_schema(
        tags=[ADMIN_TAG], summary="Reactivate a user",
        description="**Reactivate.** The user can log in again. 200 returns the user with `is_active: true`.\n\n" + ADMIN_AUTH_NOTE,
        parameters=[ACCOUNT_ID_PARAM], request=None,
        responses={200: OpenApiResponse(response=AdminUserSerializer, examples=[OpenApiExample("Reactivated", value=USER_EXAMPLE)]), 401: ADMIN_401, 403: ADMIN_403, 404: ACCOUNT_404},
    )
    @action(detail=True, methods=["post"])
    def reactivate(self, request, pk=None):
        return self._set_active(request, True)

    @extend_schema(
        tags=[ADMIN_TAG],
        summary="Send a password reset to a user",
        description=(
            "**Reset Password.** Emails the user the same 6-digit reset code as the app's Forgot Password screen; "
            "they finish on the app's Verify Code and New Password screens (the code lasts 10 minutes). Staff never "
            "see or set the new password.\n\n"
            "**Errors:** a second request within 60 seconds is 429 with `retry_after` (seconds); a deactivated "
            "account is 400 (reactivate first); 503 when the code can't be sent right now.\n\n" + ADMIN_AUTH_NOTE
        ),
        parameters=[ACCOUNT_ID_PARAM], request=None,
        responses={
            200: OpenApiResponse(response=OpenApiTypes.OBJECT, examples=[OpenApiExample("Sent", value={"message": RESET_SENT_MESSAGE.format(email="ada.obi@ikejacoop.ng")})]),
            400: OpenApiResponse(response=OpenApiTypes.OBJECT, examples=[OpenApiExample("Deactivated", value={"error": [DEACTIVATED_RESET_MESSAGE]})]),
            401: ADMIN_401, 403: ADMIN_403, 404: ACCOUNT_404,
            429: OpenApiResponse(response=OpenApiTypes.OBJECT, examples=[OpenApiExample("Too soon", value={"error": "Please wait before requesting a new code.", "retry_after": 42})]),
        },
    )
    @action(detail=True, methods=["post"], url_path="reset-password")
    def reset_password(self, request, pk=None):
        return self._reset_password(request)


class StaffAdminViewSet(_AccountAdminViewSet):
    """Admin Control Panel: Staff screen. The platform owner only."""

    kind = "staff"
    row_serializer = StaffSerializer
    update_serializer = StaffUpdateSerializer
    permission_classes = [IsAuthenticated, PasswordChangeNotRequired, IsAxiomVaultStaff, IsPlatformOwner]
    serializer_class = StaffSerializer

    def get_queryset(self):
        # Staff accounts only; owner accounts can't be edited or deactivated from here.
        return User.objects.filter(is_staff=True, is_superuser=False).order_by("-date_joined")

    OWNER_NOTE = (
        "**Authentication:** the platform **owner's** admin access token. Staff get 403 `owner_only`; Cooperative "
        "accounts get 403 `not_admin`. Owner accounts are never listed or changed here."
    )

    @extend_schema(
        tags=[ADMIN_TAG],
        summary="List staff accounts",
        description=(
            "**Staff screen** (owner only): AxiomVault support and operations accounts, newest first. "
            "`search` matches part of the name or email.\n\n**Paginated:** 20 a page.\n\n" + OWNER_NOTE
        ),
        parameters=[
            OpenApiParameter("search", OpenApiTypes.STR, description="Part of the name or email."),
            OpenApiParameter("page", OpenApiTypes.INT, description="Page number, from 1."),
            OpenApiParameter("page_size", OpenApiTypes.INT, description="Items per page, default 20, at most 100."),
        ],
        responses={200: OpenApiResponse(response=StaffSerializer(many=True), examples=[OpenApiExample("Staff", value=_paged([STAFF_EXAMPLE]))]), 401: ADMIN_401, 403: OWNER_ONLY_RESPONSE},
    )
    def list(self, request):
        staff = self.get_queryset()
        text = request.query_params.get("search", "").strip()
        if text:
            staff = staff.filter(Q(full_name__icontains=text) | Q(email__icontains=text))
        page = self.paginate_queryset(staff)
        return self.get_paginated_response(StaffSerializer(page, many=True).data)

    @extend_schema(
        tags=[ADMIN_TAG],
        summary="Create a staff account",
        description=(
            "**Add Staff** (owner only). Creates an AxiomVault staff account that can sign in to the Admin Control "
            "Panel (`admin_role: staff`). Fields: `full_name`, `email` (unique), `password` (temporary; share it "
            "yourself). They must replace it on first login. 201 returns the account. Recorded in the audit log.\n\n"
            + OWNER_NOTE
        ),
        request=StaffCreateSerializer,
        examples=[OpenApiExample("Create", value={"full_name": "Kemi Support", "email": "kemi@axiomvault.ng", "password": "Temp-Pass-2026!"}, request_only=True)],
        responses={
            201: OpenApiResponse(response=StaffSerializer, examples=[OpenApiExample("Created", value=STAFF_EXAMPLE)]),
            400: OpenApiResponse(response=OpenApiTypes.OBJECT, examples=[OpenApiExample("Email taken", value={"email": [EMAIL_TAKEN_MESSAGE]})]),
            401: ADMIN_401, 403: OWNER_ONLY_RESPONSE,
        },
    )
    def create(self, request):
        serializer = StaffCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        account = AdminAccountService.create(serializer.validated_data, is_staff=True)
        self._audit(AuditLog.USER_CREATED, account, request)
        return Response(StaffSerializer(account).data, status=status.HTTP_201_CREATED)

    @extend_schema(tags=[ADMIN_TAG], summary="View a staff account", description="One staff account (owner only).\n\n" + OWNER_NOTE,
                   parameters=[ACCOUNT_ID_PARAM], responses={200: OpenApiResponse(response=StaffSerializer, examples=[OpenApiExample("Staff", value=STAFF_EXAMPLE)]), 401: ADMIN_401, 403: OWNER_ONLY_RESPONSE, 404: ACCOUNT_404})
    def retrieve(self, request, pk=None):
        return self._retrieve(request)

    @extend_schema(
        tags=[ADMIN_TAG], summary="Edit a staff account",
        description="Changes `full_name` and/or `email` (unique). 200 returns the account. Recorded in the audit log.\n\n" + OWNER_NOTE,
        parameters=[ACCOUNT_ID_PARAM], request=StaffUpdateSerializer,
        examples=[OpenApiExample("Edit", value={"full_name": "Kemi Adeyemi"}, request_only=True)],
        responses={200: OpenApiResponse(response=StaffSerializer, examples=[OpenApiExample("Edited", value={**STAFF_EXAMPLE, "full_name": "Kemi Adeyemi"})]),
                   400: OpenApiResponse(response=OpenApiTypes.OBJECT, examples=[OpenApiExample("Email taken", value={"email": [EMAIL_TAKEN_MESSAGE]})]),
                   401: ADMIN_401, 403: OWNER_ONLY_RESPONSE, 404: ACCOUNT_404},
    )
    def partial_update(self, request, pk=None):
        return self._update(request)

    @extend_schema(
        tags=[ADMIN_TAG], summary="Deactivate a staff account",
        description="They can't sign in to the panel any more and are signed out everywhere. 200 returns the account.\n\n" + OWNER_NOTE,
        parameters=[ACCOUNT_ID_PARAM], request=None,
        responses={200: OpenApiResponse(response=StaffSerializer, examples=[OpenApiExample("Deactivated", value={**STAFF_EXAMPLE, "is_active": False})]), 401: ADMIN_401, 403: OWNER_ONLY_RESPONSE, 404: ACCOUNT_404},
    )
    @action(detail=True, methods=["post"])
    def deactivate(self, request, pk=None):
        return self._set_active(request, False)

    @extend_schema(
        tags=[ADMIN_TAG], summary="Reactivate a staff account",
        description="They can sign in again. 200 returns the account.\n\n" + OWNER_NOTE,
        parameters=[ACCOUNT_ID_PARAM], request=None,
        responses={200: OpenApiResponse(response=StaffSerializer, examples=[OpenApiExample("Reactivated", value=STAFF_EXAMPLE)]), 401: ADMIN_401, 403: OWNER_ONLY_RESPONSE, 404: ACCOUNT_404},
    )
    @action(detail=True, methods=["post"])
    def reactivate(self, request, pk=None):
        return self._set_active(request, True)

    @extend_schema(
        tags=[ADMIN_TAG], summary="Send a password reset to a staff member",
        description=(
            "Emails them the app's 6-digit reset code; they finish on the reset screens. 429 with `retry_after` "
            "within 60 seconds of the last code; 400 when deactivated.\n\n" + OWNER_NOTE
        ),
        parameters=[ACCOUNT_ID_PARAM], request=None,
        responses={200: OpenApiResponse(response=OpenApiTypes.OBJECT, examples=[OpenApiExample("Sent", value={"message": RESET_SENT_MESSAGE.format(email="kemi@axiomvault.ng")})]),
                   400: OpenApiResponse(response=OpenApiTypes.OBJECT, examples=[OpenApiExample("Deactivated", value={"error": [DEACTIVATED_RESET_MESSAGE]})]),
                   401: ADMIN_401, 403: OWNER_ONLY_RESPONSE, 404: ACCOUNT_404,
                   429: OpenApiResponse(response=OpenApiTypes.OBJECT, examples=[OpenApiExample("Too soon", value={"error": "Please wait before requesting a new code.", "retry_after": 42})])},
    )
    @action(detail=True, methods=["post"], url_path="reset-password")
    def reset_password(self, request, pk=None):
        return self._reset_password(request)


ROLES = [
    {
        "role": "owner", "panel": "admin",
        "description": "The AxiomVault platform owner (Django superuser). One person in practice.",
        "permissions": [
            "Everything AxiomVault staff can do",
            "Create, edit, deactivate, and reset staff accounts",
        ],
    },
    {
        "role": "staff", "panel": "admin",
        "description": "AxiomVault support and operations, created by the owner.",
        "permissions": [
            "Sign in to the Admin Control Panel",
            "List, create, rename, suspend, and reactivate organisations",
            "Manage Cooperative users in every organisation and send them password resets",
        ],
    },
    {
        "role": "super_admin", "panel": "app",
        "description": "Represents a Cooperative; signs up and creates it. One per organisation.",
        "permissions": [
            "Everything a team member can do, in their own organisation",
            "Add, edit, deactivate, and reactivate team members",
        ],
    },
    {
        "role": "team_member", "panel": "app",
        "description": "A Cooperative's staff, added by its super admin. Their job title is free text and grants nothing.",
        "permissions": [
            "Upload payroll, vendor, and payment files and files for evidence",
            "Work alerts and cases: review, notes, comments, evidence, escalation",
            "See the dashboard and analytics",
        ],
    },
]


class RolesViewSet(viewsets.ViewSet):
    """Admin Control Panel: the roles and what each may do (read-only)."""

    permission_classes = [IsAuthenticated, PasswordChangeNotRequired, IsAxiomVaultStaff]

    @extend_schema(
        tags=[ADMIN_TAG],
        summary="Roles and permissions",
        description=(
            "**Roles screen.** The four roles on AxiomVault and what each may do. They are fixed rules, not editable "
            "settings: `panel` says where the role signs in (`admin` = Admin Control Panel, `app` = the Cooperative "
            "app). A team member's job title (`role` on the user) is a label only.\n\n" + ADMIN_AUTH_NOTE
        ),
        responses={200: OpenApiResponse(response=OpenApiTypes.OBJECT, examples=[OpenApiExample("Roles", value={"roles": ROLES})]), 401: ADMIN_401, 403: ADMIN_403},
    )
    def list(self, request):
        return Response({"roles": ROLES})
