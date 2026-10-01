from rest_framework import viewsets, status
from rest_framework.response import Response
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiExample, OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers
from django.contrib.auth import get_user_model
from .services import (
    ACCOUNT_EXISTS_MESSAGE,
    CODE_EXPIRED_MESSAGE,
    CODE_TOO_SOON_MESSAGE,
    DELIVERY_FAILED_MESSAGE,
    INCORRECT_CODE_MESSAGE,
    MAX_CODE_ATTEMPTS,
    NO_ACTIVE_CODE_MESSAGE,
    SIGNUP_TOKEN_MAX_AGE_SECONDS,
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

User = get_user_model()

SEND_OTP_SUCCESS_MESSAGE = "OTP verification code transmitted successfully."
VERIFY_OTP_SUCCESS_MESSAGE = "OTP validation verified successfully."
RESEND_OTP_SUCCESS_MESSAGE = "A fresh OTP code has been issued."

MessageResponse = inline_serializer("MessageResponse", {"message": serializers.CharField()})
ErrorResponse = inline_serializer("ErrorResponse", {"error": serializers.CharField()})
RetryErrorResponse = inline_serializer(
    "RetryErrorResponse",
    {"error": serializers.CharField(), "retry_after": serializers.IntegerField()},
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
        email = serializer.validated_data["email"]

        token = OTPService.verify_signup_code(email, serializer.validated_data["code"])

        # Accounts made by the old signup-first flow still get marked verified until 2c replaces it.
        User.objects.filter(email__iexact=email).update(is_verified=True)
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

    @extend_schema(request=SignupSerializer, tags=["Auth"])
    def signup(self, request):
        serializer = SignupSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        AuthService.signup(serializer.validated_data)
        return Response(
            {"message": "Account created successfully. A verification code has been dispatched."},
            status=status.HTTP_201_CREATED
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
