from rest_framework import viewsets, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiExample, OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers
from .services import (
    ACCOUNT_EXISTS_MESSAGE,
    CODE_EXPIRED_MESSAGE,
    CODE_TOO_SOON_MESSAGE,
    EMAIL_NOT_VERIFIED_MESSAGE,
    INVALID_CREDENTIALS_MESSAGE,
    DELIVERY_FAILED_MESSAGE,
    INCORRECT_CODE_MESSAGE,
    LOGOUT_SUCCESS_MESSAGE,
    LOGOUT_TOKEN_INVALID_MESSAGE,
    LOGOUT_WRONG_ACCOUNT_MESSAGE,
    MAX_CODE_ATTEMPTS,
    NO_ACTIVE_CODE_MESSAGE,
    SESSION_EXPIRED_MESSAGE,
    SIGNUP_SUCCESS_MESSAGE,
    SIGNUP_TOKEN_MAX_AGE_SECONDS,
    TOKEN_INVALID_MESSAGE,
    TOO_MANY_ATTEMPTS_MESSAGE,
    VERIFICATION_CODE_COOLDOWN_SECONDS,
    OTPService,
    AuthService,
)
from .serializers import (
    SendOTPSerializer,
    VerifyOTPSerializer,
    ResendOTPSerializer,
    SignupSerializer,
    LoginSerializer,
    LogoutSerializer,
    RefreshTokenSerializer,
    ResetPasswordSerializer
)

SEND_OTP_SUCCESS_MESSAGE = "OTP verification code transmitted successfully."
VERIFY_OTP_SUCCESS_MESSAGE = "OTP validation verified successfully."
RESEND_OTP_SUCCESS_MESSAGE = "A fresh OTP code has been issued."

MessageResponse = inline_serializer("MessageResponse", {"message": serializers.CharField()})
ErrorResponse = inline_serializer("ErrorResponse", {"error": serializers.CharField()})
RetryErrorResponse = inline_serializer(
    "RetryErrorResponse",
    {"error": serializers.CharField(), "retry_after": serializers.IntegerField()},
)
LoginUserSchema = inline_serializer(
    "LoginUser",
    {
        "id": serializers.UUIDField(help_text="The user's permanent ID."),
        "email": serializers.EmailField(help_text="Login email, stored in lowercase for new accounts."),
        "full_name": serializers.CharField(help_text="Name shown in the app."),
        "organization": serializers.CharField(help_text="Organization entered at registration."),
        "department": serializers.CharField(
            help_text="Department value, e.g. `internal_audit`, `finance`, `compliance`, "
            "`risk_management`, `human_resources`, `others`."
        ),
        "role": serializers.CharField(
            help_text="`fraud_analyst`, `compliance_officer`, `auditor`, `manager`, or an empty "
            "string when an admin has not assigned a role yet."
        ),
    },
)
LoginResponse = inline_serializer(
    "LoginResponse",
    {
        "access": serializers.CharField(
            help_text="JWT access token, valid for 30 minutes. Send it on every protected request "
            "as `Authorization: Bearer <access>`."
        ),
        "refresh": serializers.CharField(
            help_text="JWT refresh token, valid for 1 day. Keep it safe; it is used to get a new "
            "access token and to log out."
        ),
        "user": LoginUserSchema,
    },
)
TokenRefreshResponse = inline_serializer(
    "TokenRefreshResponse",
    {
        "access": serializers.CharField(
            help_text="New JWT access token, valid for 30 minutes. Replace the old one and send it as "
            "`Authorization: Bearer <access>`."
        ),
    },
)
SignupResponse = inline_serializer(
    "SignupResponse", {"message": serializers.CharField(), "email": serializers.EmailField()}
)
VerifiedResponse = inline_serializer(
    "VerifiedResponse",
    {
        "message": serializers.CharField(),
        "verification_token": serializers.CharField(),
        "expires_in": serializers.IntegerField(),
    },
)


def code_request_responses(success_message, success_description):
    """Responses shared by send-otp and resend-otp, which follow the same rules."""
    return {
        200: OpenApiResponse(
            response=MessageResponse,
            description=success_description,
            examples=[OpenApiExample("Sent", value={"message": success_message})],
        ),
        400: OpenApiResponse(
            response=OpenApiTypes.OBJECT,
            description="Invalid email, or the email already has an account.",
            examples=[
                OpenApiExample("Invalid email", value={"email": ["Enter a valid email address."]}),
                OpenApiExample("Account exists", value={"error": ACCOUNT_EXISTS_MESSAGE}),
            ],
        ),
        429: OpenApiResponse(
            response=RetryErrorResponse,
            description="A code was sent to this email less than a minute ago. Retry after `retry_after` seconds.",
            examples=[OpenApiExample("Too soon", value={"error": CODE_TOO_SOON_MESSAGE, "retry_after": 42})],
        ),
        503: OpenApiResponse(
            response=ErrorResponse,
            description="The email could not be queued. No code was created.",
            examples=[OpenApiExample("Email unavailable", value={"error": DELIVERY_FAILED_MESSAGE})],
        ),
    }


class AuthViewSet(viewsets.ViewSet):
    """Account endpoints. Only logout needs a logged-in user; the rest are public."""

    serializer_class = SendOTPSerializer
    AUTHENTICATED_ACTIONS = {"logout"}

    def initialize_request(self, request, *args, **kwargs):
        # DRF picks authenticators before it sets self.action, so work out the action first.
        self.action = self.action_map.get(request.method.lower())
        return super().initialize_request(request, *args, **kwargs)

    def get_authenticators(self):
        # Public endpoints ignore any Authorization header, so a stale token kept by the
        # frontend cannot turn login or signup into a 401.
        if getattr(self, "action", None) in self.AUTHENTICATED_ACTIONS:
            return super().get_authenticators()
        return []

    def get_permissions(self):
        # Logged-in actions answer 401 without a valid access token; the rest stay open.
        if getattr(self, "action", None) in self.AUTHENTICATED_ACTIONS:
            return [IsAuthenticated()]
        return super().get_permissions()

    @extend_schema(
        tags=["Auth"],
        summary="Send a sign-up verification code",
        description=(
            "**Step 1 of email-first registration** (Figma screen *Get Started*, button "
            "*Send Verification Code*).\n\n"
            "Emails a 6-digit verification code to the work email address. The code is valid for "
            "**2 minutes**. The next screen (*Verify Code*) sends it to `verify-otp`.\n\n"
            "**Request field**\n"
            "- `email` (required): any valid email address. Letter case does not matter.\n\n"
            "**Rules**\n"
            "- An email that already belongs to a **verified** account is refused (400 *Account "
            "exists*); show the *Sign In* link. An account left **unverified** by the old signup "
            "flow does not count: that person can register again and their old account is "
            "replaced at the end of registration.\n"
            f"- One code per email every **{VERIFICATION_CODE_COOLDOWN_SECONDS} seconds** (letter "
            "case ignored). A request inside that window gets 429 with `retry_after` = seconds to "
            "wait; use it for the *Resend in N seconds* countdown.\n"
            "- A new code cancels any older unused code for the same email.\n\n"
            "**Responses**\n"
            "- `200`: the code was saved and the email was queued. Move to *Verify Code*.\n"
            "- `400`: invalid email field, or a verified account already uses the email.\n"
            "- `429`: too soon; wait `retry_after` seconds.\n"
            "- `503`: the email could not be queued (mail queue unavailable). No code was created; "
            "ask the user to try again shortly.\n\n"
            "**Authentication:** none. Any `Authorization` header is ignored."
        ),
        request=SendOTPSerializer,
        examples=[
            OpenApiExample("New work email", value={"email": "auditor@agency.gov"}, request_only=True),
        ],
        responses=code_request_responses(SEND_OTP_SUCCESS_MESSAGE, "Code created and email queued."),
    )
    def send_otp(self, request):
        serializer = SendOTPSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        OTPService.send_verification_code(serializer.validated_data["email"])

        return Response({"message": SEND_OTP_SUCCESS_MESSAGE}, status=status.HTTP_200_OK)

    @extend_schema(
        tags=["Auth"],
        summary="Verify a sign-up code",
        description=(
            "Step 2 of email-first registration (the 'Verify Code' screen). Checks the newest "
            "code sent to this email. On success returns a `verification_token` that the "
            f"registration form must send; it is valid for {SIGNUP_TOKEN_MAX_AGE_SECONDS // 60} minutes. "
            f"After {MAX_CODE_ATTEMPTS} wrong codes the code is cancelled and a new one must be "
            "requested. No authentication required."
        ),
        request=VerifyOTPSerializer,
        examples=[
            OpenApiExample(
                "Code from the email",
                value={"email": "auditor@agency.gov", "code": "482913"},
                request_only=True,
            ),
        ],
        responses={
            200: OpenApiResponse(
                response=VerifiedResponse,
                description="Code accepted. Keep the token for the registration form.",
                examples=[
                    OpenApiExample(
                        "Verified",
                        value={
                            "message": VERIFY_OTP_SUCCESS_MESSAGE,
                            "verification_token": "auditor@agency.gov:1tQx9z:Hk3...signature",
                            "expires_in": SIGNUP_TOKEN_MAX_AGE_SECONDS,
                        },
                    )
                ],
            ),
            400: OpenApiResponse(
                response=OpenApiTypes.OBJECT,
                description="Invalid input, or the code was wrong, expired, cancelled, or never sent.",
                examples=[
                    OpenApiExample("Invalid code format", value={"code": ["Enter the 6-digit code."]}),
                    OpenApiExample("Wrong code", value={"error": INCORRECT_CODE_MESSAGE, "attempts_remaining": 2}),
                    OpenApiExample("Too many attempts", value={"error": TOO_MANY_ATTEMPTS_MESSAGE}),
                    OpenApiExample("Expired", value={"error": CODE_EXPIRED_MESSAGE}),
                    OpenApiExample("No active code", value={"error": NO_ACTIVE_CODE_MESSAGE}),
                ],
            ),
        },
    )
    def verify_otp(self, request):
        serializer = VerifyOTPSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        token = OTPService.verify_signup_code(
            serializer.validated_data["email"], serializer.validated_data["code"]
        )

        return Response(
            {
                "message": VERIFY_OTP_SUCCESS_MESSAGE,
                "verification_token": token,
                "expires_in": SIGNUP_TOKEN_MAX_AGE_SECONDS,
            },
            status=status.HTTP_200_OK,
        )

    @extend_schema(
        tags=["Auth"],
        summary="Resend a sign-up verification code",
        description=(
            "**Resend link on the *Verify Code* screen**, shown when the *Resend in N seconds* "
            "countdown reaches zero.\n\n"
            "Sends a fresh 6-digit code (valid for 2 minutes) and cancels the previous one, so only "
            "the newest code works. It follows exactly the same rules and responses as `send-otp`:\n"
            "- the email must not belong to a **verified** account (400 *Account exists*);\n"
            f"- one code per email every **{VERIFICATION_CODE_COOLDOWN_SECONDS} seconds** "
            "(429 with `retry_after`);\n"
            "- 503 if the email could not be queued (no code is created).\n\n"
            "**Request field**\n"
            "- `email` (required): the same address used on the *Get Started* screen.\n\n"
            "**Authentication:** none. Any `Authorization` header is ignored."
        ),
        request=ResendOTPSerializer,
        examples=[
            OpenApiExample("Resend", value={"email": "auditor@agency.gov"}, request_only=True),
        ],
        responses=code_request_responses(RESEND_OTP_SUCCESS_MESSAGE, "New code created and email queued."),
    )
    def resend_otp(self, request):
        serializer = ResendOTPSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        OTPService.send_verification_code(serializer.validated_data["email"])
        return Response({"message": RESEND_OTP_SUCCESS_MESSAGE}, status=status.HTTP_200_OK)

    @extend_schema(
        tags=["Auth"],
        summary="Create an account (complete registration)",
        description=(
            "**Step 3 of email-first registration** (Figma screen *Create Your Account*, button "
            "*Create Account*).\n\n"
            "Creates the account for the email that was verified with `verify-otp`. The email is "
            "**not** sent in this form: it is read from `verification_token`, so nobody can verify "
            "one address and register another. It is stored in lowercase.\n\n"
            "**Request fields**\n"
            f"- `verification_token` (required): from the `verify-otp` response; valid for "
            f"{SIGNUP_TOKEN_MAX_AGE_SECONDS // 60} minutes after verification.\n"
            "- `full_name` (required, max 255 characters).\n"
            "- `organization` (required, max 255 characters).\n"
            "- `department` (required): one of `internal_audit`, `finance`, `compliance`, "
            "`risk_management`, `human_resources`, `others` (the dropdown values).\n"
            "- `password` (required): at least 8 characters, not a common password, not only "
            "numbers, and not too similar to the full name or email.\n"
            "- `re_enter_password` (required): must equal `password`.\n\n"
            "The *Terms of Service* checkbox is enforced by the frontend only and is not sent.\n\n"
            "**What gets created**\n"
            "- A verified account with **no role**; an admin assigns the role later in `/admin/`.\n"
            "- If the email belongs to an account left **unverified** by the old signup flow, that "
            "account is updated in place instead (new details and password, verified, role "
            "cleared).\n\n"
            "**Responses**\n"
            "- `201`: account created; send the user to *Sign In*.\n"
            "- `400`: a field error, passwords that differ, a weak password, an invalid or expired "
            "token (verify the email again), or a verified account already uses the email.\n\n"
            "**Authentication:** none. Any `Authorization` header is ignored."
        ),
        request=SignupSerializer,
        examples=[
            OpenApiExample(
                "Registration form",
                value={
                    "verification_token": "<verification_token from verify-otp>",
                    "full_name": "Juan dela Cruz",
                    "organization": "Agency Name",
                    "department": "internal_audit",
                    "password": "Str0ng-Passw0rd!",
                    "re_enter_password": "Str0ng-Passw0rd!",
                },
                request_only=True,
            ),
        ],
        responses={
            201: OpenApiResponse(
                response=SignupResponse,
                description="Account created. Send the user to Sign In.",
                examples=[
                    OpenApiExample(
                        "Created",
                        value={"message": SIGNUP_SUCCESS_MESSAGE, "email": "juan@agency.gov"},
                    )
                ],
            ),
            400: OpenApiResponse(
                response=OpenApiTypes.OBJECT,
                description="Invalid form fields, weak password, bad or expired token, or the email already has an account.",
                examples=[
                    OpenApiExample("Missing field", value={"full_name": ["This field is required."]}),
                    OpenApiExample("Unknown department", value={"department": ['"hr" is not a valid choice.']}),
                    OpenApiExample("Passwords differ", value={"re_enter_password": ["Passwords do not match."]}),
                    OpenApiExample(
                        "Weak password",
                        value={"password": ["This password is too short. It must contain at least 8 characters."]},
                    ),
                    OpenApiExample("Token invalid or expired", value={"error": TOKEN_INVALID_MESSAGE}),
                    OpenApiExample("Account exists", value={"error": ACCOUNT_EXISTS_MESSAGE}),
                ],
            ),
        },
    )
    def signup(self, request):
        serializer = SignupSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        user = AuthService.signup(serializer.validated_data)
        return Response(
            {"message": SIGNUP_SUCCESS_MESSAGE, "email": user.email},
            status=status.HTTP_201_CREATED,
        )

    @extend_schema(
        tags=["Auth"],
        summary="Log in and get JWT tokens",
        description=(
            "**Sign In screen.** Checks the email and password and returns two JWT tokens plus "
            "the user's profile.\n\n"
            "**Request fields**\n"
            "- `email` (required): the account's email. Letter case does not matter.\n"
            "- `password` (required).\n\n"
            "**Using the tokens**\n"
            "- `access` is valid for **30 minutes**. Send it on every protected request in the "
            "header `Authorization: Bearer <access>`.\n"
            "- `refresh` is valid for **1 day**. Use it to get a new access token when the access "
            "token expires, and send it when logging out. Store both securely; never put them in "
            "URLs or logs.\n\n"
            "**The `user` object**\n"
            "- Use `full_name` for display. `role` is an empty string until an admin assigns one; "
            "the frontend can show a *waiting for access* message in that case.\n\n"
            "**Responses**\n"
            "- `200`: logged in; store the tokens and continue into the app.\n"
            "- `400`: a field is missing or the email is not a valid address.\n"
            "- `401`: wrong email or password, or the account is deactivated. The message is the "
            "same in every case on purpose, so the API never reveals which emails have accounts.\n"
            "- `403`: the password was correct but the email was never verified (an account from "
            "the old signup flow). Send the user to *Get Started* to register again; their old "
            "account is replaced.\n\n"
            "**Authentication:** none. Any `Authorization` header is ignored."
        ),
        request=LoginSerializer,
        examples=[
            OpenApiExample(
                "Sign in",
                value={"email": "juan@agency.gov", "password": "Bright-Ledger-2026!"},
                request_only=True,
            ),
            OpenApiExample(
                "Email in any letter case",
                value={"email": "Juan@Agency.GOV", "password": "Bright-Ledger-2026!"},
                request_only=True,
            ),
        ],
        responses={
            200: OpenApiResponse(
                response=LoginResponse,
                description="Logged in. Store both tokens.",
                examples=[
                    OpenApiExample(
                        "Logged in",
                        value={
                            "access": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...access",
                            "refresh": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...refresh",
                            "user": {
                                "id": "3f2a7c9e-1b4d-4e8a-9c2f-6d5e4b3a2c1d",
                                "email": "juan@agency.gov",
                                "full_name": "Juan dela Cruz",
                                "organization": "Agency Name",
                                "department": "internal_audit",
                                "role": "",
                            },
                        },
                    )
                ],
            ),
            400: OpenApiResponse(
                response=OpenApiTypes.OBJECT,
                description="A field is missing or invalid.",
                examples=[
                    OpenApiExample("Missing password", value={"password": ["This field is required."]}),
                    OpenApiExample("Invalid email", value={"email": ["Enter a valid email address."]}),
                ],
            ),
            401: OpenApiResponse(
                response=ErrorResponse,
                description="Wrong email or password, or a deactivated account.",
                examples=[OpenApiExample("Invalid credentials", value={"error": INVALID_CREDENTIALS_MESSAGE})],
            ),
            403: OpenApiResponse(
                response=ErrorResponse,
                description="Correct password, but the email was never verified. Register again.",
                examples=[OpenApiExample("Email not verified", value={"error": EMAIL_NOT_VERIFIED_MESSAGE})],
            ),
        },
    )
    def login(self, request):
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        result = AuthService.login(
            request,
            email=serializer.validated_data["email"],
            password=serializer.validated_data["password"],
        )
        return Response(result, status=status.HTTP_200_OK)

    @extend_schema(
        tags=["Auth"],
        summary="Get a new access token with the refresh token",
        description=(
            "**Keeps the user signed in.** The access token from login lasts only 30 minutes. When a "
            "protected request answers `401`, the frontend calls this endpoint with the refresh token, "
            "stores the new access token, and retries the original request. The user sees nothing.\n\n"
            "**Request fields**\n"
            "- `refresh` (required): the `refresh` token returned by `POST /api/v1/auth/login/`. It is "
            "valid for **1 day** after login.\n\n"
            "**What comes back**\n"
            "- Only a new `access` token, valid for **30 minutes**. Send it on every protected request in "
            "the header `Authorization: Bearer <access>`.\n"
            "- There is **no new refresh token**. Keep using the one from login until it expires; after "
            "that the user must log in again.\n\n"
            "**Responses**\n"
            "- `200`: success; replace the stored access token and retry the request that failed.\n"
            "- `400`: `refresh` is missing or empty. This is a frontend bug, not an expired session.\n"
            "- `401`: the refresh token is expired, invalid, an access token instead of a refresh token, "
            "or belongs to an account that was deactivated or deleted. The message is the same in every "
            "case. A refresh token that was used to log out (`POST /api/v1/auth/logout/`) also gets this "
            "`401`. Clear both stored tokens and send the user to the Sign In screen.\n\n"
            "**Authentication:** none. Do not send the expired access token; any `Authorization` header "
            "is ignored."
        ),
        request=RefreshTokenSerializer,
        examples=[
            OpenApiExample(
                "Refresh",
                value={"refresh": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...refresh"},
                request_only=True,
            ),
        ],
        responses={
            200: OpenApiResponse(
                response=TokenRefreshResponse,
                description="New access token issued. Replace the stored one.",
                examples=[
                    OpenApiExample(
                        "New access token",
                        value={"access": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...access"},
                    )
                ],
            ),
            400: OpenApiResponse(
                response=OpenApiTypes.OBJECT,
                description="`refresh` is missing or empty.",
                examples=[
                    OpenApiExample("Missing refresh", value={"refresh": ["This field is required."]}),
                    OpenApiExample("Empty refresh", value={"refresh": ["This field may not be blank."]}),
                ],
            ),
            401: OpenApiResponse(
                response=ErrorResponse,
                description="Refresh token expired or not usable. Log in again.",
                examples=[OpenApiExample("Session expired", value={"error": SESSION_EXPIRED_MESSAGE})],
            ),
        },
    )
    def token_refresh(self, request):
        serializer = RefreshTokenSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        result = AuthService.refresh_access_token(serializer.validated_data["refresh"])
        return Response(result, status=status.HTTP_200_OK)

    @extend_schema(request=SendOTPSerializer, tags=["Auth"])
    def forgot_password(self, request):
        serializer = SendOTPSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        if AuthService.forgot_password(serializer.validated_data["email"]):
            return Response({"message": "Password modification code dispatched."}, status=status.HTTP_200_OK)
        return Response({"error": "No account tied to this email address."}, status=status.HTTP_404_NOT_FOUND)

    @extend_schema(request=ResetPasswordSerializer, tags=["Auth"])
    def reset_password(self, request):
        serializer = ResetPasswordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        AuthService.reset_password(
            email=serializer.validated_data["email"],
            code=serializer.validated_data["otp_code"],
            new_password=serializer.validated_data["new_password"]
        )
        return Response({"message": "Password altered successfully."}, status=status.HTTP_200_OK)

    @extend_schema(
        tags=["Auth"],
        summary="Log out and block the refresh token",
        description=(
            "**Log out button.** Ends the session by adding the refresh token to the server's blacklist. "
            "After this, the refresh token can never be used again: `POST /api/v1/auth/token/refresh/` "
            "answers `401` for it.\n\n"
            "**Authentication:** required. Send the current access token as "
            "`Authorization: Bearer <access>`. If the access token has expired, call "
            "`token/refresh/` first, then log out with the new access token.\n\n"
            "**Request fields**\n"
            "- `refresh` (required): the `refresh` token returned by `POST /api/v1/auth/login/` for this "
            "same account.\n\n"
            "**What the frontend should do**\n"
            "- Delete **both** stored tokens after the call, whatever the response. The access token is "
            "not blocked by the server; it simply stops working when it expires (at most 30 minutes), "
            "so the frontend must forget it.\n\n"
            "**Responses**\n"
            "- `200`: logged out; the refresh token is blocked. Go to the Sign In screen.\n"
            "- `400`: `refresh` is missing or empty, or the refresh token is expired, invalid, an access "
            "token, or was already used to log out. The session is already over; clear the tokens and "
            "go to Sign In. (This is `400`, not `401`, so a frontend that retries on `401` does not loop.)\n"
            "- `401`: the `Authorization` header is missing, or the access token is expired or invalid. "
            "This body uses DRF's standard `detail` shape.\n"
            "- `403`: the refresh token belongs to a different account than the access token. Nothing "
            "was blocked."
        ),
        request=LogoutSerializer,
        examples=[
            OpenApiExample(
                "Log out",
                value={"refresh": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...refresh"},
                request_only=True,
            ),
        ],
        responses={
            200: OpenApiResponse(
                response=MessageResponse,
                description="Logged out. The refresh token is blacklisted.",
                examples=[OpenApiExample("Logged out", value={"message": LOGOUT_SUCCESS_MESSAGE})],
            ),
            400: OpenApiResponse(
                response=OpenApiTypes.OBJECT,
                description="Missing field, or the refresh token cannot be used (already logged out, expired, invalid).",
                examples=[
                    OpenApiExample("Missing refresh", value={"refresh": ["This field is required."]}),
                    OpenApiExample("Session already ended", value={"error": LOGOUT_TOKEN_INVALID_MESSAGE}),
                ],
            ),
            401: OpenApiResponse(
                response=OpenApiTypes.OBJECT,
                description="No access token, or the access token is expired or invalid.",
                examples=[
                    OpenApiExample(
                        "No access token",
                        value={"detail": "Authentication credentials were not provided."},
                    ),
                    OpenApiExample(
                        "Access token expired or invalid",
                        value={
                            "detail": "Given token not valid for any token type",
                            "code": "token_not_valid",
                            "messages": [
                                {
                                    "token_class": "AccessToken",
                                    "token_type": "access",
                                    "message": "Token is expired",
                                }
                            ],
                        },
                    ),
                ],
            ),
            403: OpenApiResponse(
                response=ErrorResponse,
                description="The refresh token belongs to a different account. Nothing was blocked.",
                examples=[OpenApiExample("Wrong account", value={"error": LOGOUT_WRONG_ACCOUNT_MESSAGE})],
            ),
        },
    )
    def logout(self, request):
        serializer = LogoutSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        AuthService.logout(request.user, serializer.validated_data["refresh"])
        return Response({"message": LOGOUT_SUCCESS_MESSAGE}, status=status.HTTP_200_OK)
