from rest_framework import viewsets, status
from rest_framework.response import Response
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiExample, OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers
from .services import (
    ACCOUNT_EXISTS_MESSAGE,
    CODE_EXPIRED_MESSAGE,
    CODE_TOO_SOON_MESSAGE,
    DELIVERY_FAILED_MESSAGE,
    INCORRECT_CODE_MESSAGE,
    MAX_CODE_ATTEMPTS,
    NO_ACTIVE_CODE_MESSAGE,
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

    serializer_class = SendOTPSerializer
    """Unified ViewSet routing requests straight to dedicated service engines."""

    @extend_schema(
        tags=["Auth"],
        summary="Send a sign-up verification code",
        description=(
            "Step 1 of email-first registration (the 'Get Started' screen). Emails a "
            "6-digit code, valid for 2 minutes, to an email address that has no account "
            f"yet. A new code for the same email can be requested every "
            f"{VERIFICATION_CODE_COOLDOWN_SECONDS} seconds; a newer code replaces older ones. "
            "No authentication required."
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
            "Sends a new 6-digit code (the 'Resend' link on the Verify Code screen). Same rules as "
            f"send-otp: no account may use the email, and one code per {VERIFICATION_CODE_COOLDOWN_SECONDS} "
            "seconds. The new code replaces older ones. No authentication required."
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
            "Step 3 of email-first registration (the 'Create Your Account' form). Send the "
            "`verification_token` from verify-otp with the form fields; the account's email is "
            "taken from the token and stored in lowercase. The token is valid for "
            f"{SIGNUP_TOKEN_MAX_AGE_SECONDS // 60} minutes after verification. New accounts are "
            "verified and have no role until an admin assigns one. Passwords need at least 8 "
            "characters, must not be too common, all numbers, or too similar to the name or "
            "email. No authentication required."
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

    @extend_schema(request=LoginSerializer, tags=["Auth"])
    def login(self, request):
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        AuthService.login(
            request, 
            email=serializer.validated_data["email"], 
            password=serializer.validated_data["password"]
        )
        return Response({"message": "Authentication successful."}, status=status.HTTP_200_OK)

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

    @extend_schema(responses={200: dict}, tags=["Auth"])
    def logout(self, request):
        AuthService.logout(request)
        return Response({"message": "Session closed safely."}, status=status.HTTP_200_OK)
