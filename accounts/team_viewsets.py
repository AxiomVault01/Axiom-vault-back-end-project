from django.shortcuts import get_object_or_404
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiExample, OpenApiResponse, extend_schema, extend_schema_view
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .models import AuditLog, User
from .permissions import (
    PASSWORD_CHANGE_REQUIRED_MESSAGE,
    SUPER_ADMIN_ONLY_MESSAGE,
    IsSuperAdmin,
    PasswordChangeNotRequired,
)
from .serializers import (
    EMAIL_TAKEN_MESSAGE,
    PHONE_NUMBER_MESSAGE,
    TeamMemberCreateSerializer,
    TeamMemberSerializer,
    TeamMemberUpdateSerializer,
)
from .services import TeamService

AUTH_NOTE = (
    "**Authentication:** `Authorization: Bearer <access_token>` of the organisation's **super admin** "
    "(`account_type: super_admin` in the login response). Team members get `403`."
)
MEMBER_EXAMPLE = {
    "id": "7c1e2f4a-9b3d-4e6f-8a2c-1d5b7e9f0a3c",
    "full_name": "Ada Obi",
    "email": "ada.obi@ikejacoop.ng",
    "phone_number": "+2348012345678",
    "role": "Payroll Analyst",
    "is_active": True,
    "must_change_password": True,
    "date_joined": "2026-10-13T09:30:00Z",
}
UNAUTHORIZED = OpenApiResponse(
    response=OpenApiTypes.OBJECT,
    description="No access token, or it is expired or invalid. Log in again.",
    examples=[OpenApiExample("No token", value={"detail": "Authentication credentials were not provided."})],
)
FORBIDDEN = OpenApiResponse(
    response=OpenApiTypes.OBJECT,
    description="The caller is not the super admin, or still has to change a temporary password.",
    examples=[
        OpenApiExample("Not the super admin", value={"error": SUPER_ADMIN_ONLY_MESSAGE}),
        OpenApiExample(
            "Temporary password not changed",
            value={"error": PASSWORD_CHANGE_REQUIRED_MESSAGE, "code": "password_change_required"},
        ),
    ],
)
NOT_FOUND = OpenApiResponse(
    response=OpenApiTypes.OBJECT,
    description="No team member with this ID in your organisation (members of other organisations are "
    "never visible).",
    examples=[OpenApiExample("Not found", value={"detail": "No User matches the given query."})],
)
MEMBER_OK = OpenApiResponse(
    response=TeamMemberSerializer, examples=[OpenApiExample("Member", value=MEMBER_EXAMPLE)]
)


@extend_schema_view(
    list=extend_schema(
        tags=["Team"],
        summary="List my team members",
        description=(
            "**Team screen.** Returns every team member of the super admin's organisation, newest first, "
            "including deactivated ones (`is_active: false`). The super admin's own account is not in the "
            "list.\n\n**Paginated:** 20 a page by default. `?page=2` for the next page, `?page_size=` up to "
            "100. The body is `{count, next, previous, results}`; follow `next` until it is `null`. A page "
            "past the end is 404.\n\n" + AUTH_NOTE
        ),
        responses={
            200: OpenApiResponse(
                response=TeamMemberSerializer(many=True),
                examples=[
                    OpenApiExample(
                        "Team", value={"count": 1, "next": None, "previous": None, "results": [MEMBER_EXAMPLE]}
                    )
                ],
            ),
            401: UNAUTHORIZED,
            403: FORBIDDEN,
        },
    ),
    retrieve=extend_schema(
        tags=["Team"],
        summary="View one team member",
        description="Returns one team member of the caller's organisation by `id`.\n\n" + AUTH_NOTE,
        responses={200: MEMBER_OK, 401: UNAUTHORIZED, 403: FORBIDDEN, 404: NOT_FOUND},
    ),
)
class TeamMemberViewSet(viewsets.GenericViewSet):
    """The super admin's team: add, list, view, edit, deactivate, and reactivate members."""

    permission_classes = [IsAuthenticated, IsSuperAdmin, PasswordChangeNotRequired]
    serializer_class = TeamMemberSerializer
    # Only tells Swagger the `id` is a UUID; requests always use get_queryset() below.
    queryset = User.objects.none()
    http_method_names = ["get", "post", "patch"]

    def get_queryset(self):
        # Scoped through the caller's own organisation only, never an ID from the request.
        return TeamService.members_of(self.request.user)

    def get_object(self):
        return get_object_or_404(self.get_queryset(), pk=self.kwargs["pk"])

    def list(self, request):
        page = self.paginate_queryset(self.get_queryset())
        return self.get_paginated_response(TeamMemberSerializer(page, many=True).data)

    def retrieve(self, request, pk=None):
        return Response(TeamMemberSerializer(self.get_object()).data)

    @extend_schema(
        tags=["Team"],
        summary="Add a team member",
        description=(
            "**Add Team Member form.** Creates a team member in the super admin's organisation. The "
            "organisation always comes from the logged-in super admin, never from the request.\n\n"
            "**Request fields** (all required)\n"
            "- `full_name`: at most 255 characters.\n"
            "- `phone_number`: 7 to 15 digits, optionally starting with `+`. Spaces and hyphens are "
            "removed before checking, so `+234 801 234 5678` is stored as `+2348012345678`.\n"
            "- `role`: the member's job title, free text up to 100 characters. It is a label only and "
            "does not change what the member can do.\n"
            "- `email`: the member's login email. It must not belong to any other account (letter case "
            "ignored) and is stored in lowercase.\n"
            "- `password`: a temporary password the super admin shares with the member. It must pass the "
            "normal rules (at least 8 characters, not common, not only numbers, not too similar to the "
            "name or email). No email is sent.\n\n"
            "**What happens next:** the member logs in with `role: team_member` and the temporary password. "
            "Login returns `must_change_password: true`; the frontend must show the change-password screen, "
            "because every other endpoint answers `403` until the password is changed.\n\n"
            "**Responses**\n"
            "- `201`: member created; the body is the new member.\n"
            "- `400`: a field is missing or invalid, the email is taken, or the password is too weak.\n"
            "- `401` / `403`: see below.\n\n" + AUTH_NOTE
        ),
        request=TeamMemberCreateSerializer,
        examples=[
            OpenApiExample(
                "New member",
                value={
                    "full_name": "Ada Obi",
                    "phone_number": "+234 801 234 5678",
                    "role": "Payroll Analyst",
                    "email": "Ada.Obi@ikejacoop.ng",
                    "password": "Temp-Ledger-2026!",
                },
                request_only=True,
            )
        ],
        responses={
            201: OpenApiResponse(
                response=TeamMemberSerializer, examples=[OpenApiExample("Created", value=MEMBER_EXAMPLE)]
            ),
            400: OpenApiResponse(
                response=OpenApiTypes.OBJECT,
                description="Invalid fields, a taken email, or a weak password.",
                examples=[
                    OpenApiExample("Email taken", value={"email": [EMAIL_TAKEN_MESSAGE]}),
                    OpenApiExample("Bad phone number", value={"phone_number": [PHONE_NUMBER_MESSAGE]}),
                    OpenApiExample("Missing field", value={"role": ["This field is required."]}),
                    OpenApiExample(
                        "Weak password",
                        value={"password": ["This password is too short. It must contain at least 8 characters."]},
                    ),
                ],
            ),
            401: UNAUTHORIZED,
            403: FORBIDDEN,
        },
    )
    def create(self, request):
        serializer = TeamMemberCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        member = TeamService.add_member(request.user, serializer.validated_data)
        AuditLog.record(
            AuditLog.TEAM_MEMBER_ADDED, actor=request.user, target=member, details={"email": member.email}, request=request
        )
        return Response(TeamMemberSerializer(member).data, status=status.HTTP_201_CREATED)

    @extend_schema(
        tags=["Team"],
        summary="Edit a team member",
        description=(
            "**Edit Team Member form.** Changes only the fields sent; every field is optional. Same rules "
            "as when adding a member. Account type, organisation, and active state cannot be changed here "
            "(they are ignored if sent); use deactivate / reactivate for the active state. The password "
            "is not edited here.\n\n"
            "**Responses:** `200` with the updated member, `400` for an invalid field or a taken email, "
            "`404` when the member is not in your organisation.\n\n" + AUTH_NOTE
        ),
        request=TeamMemberUpdateSerializer,
        examples=[
            OpenApiExample(
                "Change role and phone",
                value={"role": "Senior Payroll Analyst", "phone_number": "08012345678"},
                request_only=True,
            )
        ],
        responses={
            200: MEMBER_OK,
            400: OpenApiResponse(
                response=OpenApiTypes.OBJECT,
                examples=[
                    OpenApiExample("Email taken", value={"email": [EMAIL_TAKEN_MESSAGE]}),
                    OpenApiExample("Bad phone number", value={"phone_number": [PHONE_NUMBER_MESSAGE]}),
                ],
            ),
            401: UNAUTHORIZED,
            403: FORBIDDEN,
            404: NOT_FOUND,
        },
    )
    def partial_update(self, request, pk=None):
        member = self.get_object()
        serializer = TeamMemberUpdateSerializer(data=request.data, context={"member": member})
        serializer.is_valid(raise_exception=True)
        changed = dict(serializer.validated_data)
        member = TeamService.update_member(member, changed)
        AuditLog.record(
            AuditLog.TEAM_MEMBER_UPDATED, actor=request.user, target=member, details={"fields": sorted(changed)}, request=request
        )
        return Response(TeamMemberSerializer(member).data)

    @extend_schema(
        tags=["Team"],
        summary="Deactivate a team member",
        description=(
            "Stops the member from logging in and ends all of their sessions at once (their refresh tokens "
            "stop working; an access token already issued is refused because the account is inactive). "
            "Nothing is deleted, so their history stays. Safe to repeat. No request body.\n\n" + AUTH_NOTE
        ),
        request=None,
        responses={200: MEMBER_OK, 401: UNAUTHORIZED, 403: FORBIDDEN, 404: NOT_FOUND},
    )
    @action(detail=True, methods=["post"])
    def deactivate(self, request, pk=None):
        member = TeamService.set_active(self.get_object(), False)
        AuditLog.record(AuditLog.TEAM_MEMBER_DEACTIVATED, actor=request.user, target=member, request=request)
        return Response(TeamMemberSerializer(member).data)

    @extend_schema(
        tags=["Team"],
        summary="Reactivate a team member",
        description=(
            "Lets a deactivated member log in again with their existing password. Safe to repeat. No "
            "request body.\n\n" + AUTH_NOTE
        ),
        request=None,
        responses={200: MEMBER_OK, 401: UNAUTHORIZED, 403: FORBIDDEN, 404: NOT_FOUND},
    )
    @action(detail=True, methods=["post"])
    def reactivate(self, request, pk=None):
        member = TeamService.set_active(self.get_object(), True)
        AuditLog.record(AuditLog.TEAM_MEMBER_REACTIVATED, actor=request.user, target=member, request=request)
        return Response(TeamMemberSerializer(member).data)
