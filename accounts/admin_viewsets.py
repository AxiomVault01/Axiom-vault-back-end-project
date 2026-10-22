"""Admin Control Panel endpoints, for AxiomVault staff only (features 19-27)."""

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiExample, OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers, status, viewsets
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

from .models import AuditLog, User
from .permissions import IsAxiomVaultStaff
from .serializers import AdminLoginSerializer, LogoutSerializer
from .services import (
    INVALID_CREDENTIALS_MESSAGE,
    LOGOUT_SUCCESS_MESSAGE,
    LOGOUT_TOKEN_INVALID_MESSAGE,
    LOGOUT_WRONG_ACCOUNT_MESSAGE,
    NOT_ADMIN_MESSAGE,
    AuthService,
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
