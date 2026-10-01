from rest_framework import viewsets, status
from rest_framework.response import Response
from drf_spectacular.utils import OpenApiExample, OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers
from django.contrib.auth import get_user_model
from .services import (
    ACCOUNT_EXISTS_MESSAGE,
    CODE_TOO_SOON_MESSAGE,
    DELIVERY_FAILED_MESSAGE,
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

MessageResponse = inline_serializer("MessageResponse", {"message": serializers.CharField()})
ErrorResponse = inline_serializer("ErrorResponse", {"error": serializers.CharField()})
RetryErrorResponse = inline_serializer(
    "RetryErrorResponse",
    {"error": serializers.CharField(), "retry_after": serializers.IntegerField()},
)


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
        responses={
            200: OpenApiResponse(
                response=MessageResponse,
                description="Code created and email queued.",
                examples=[OpenApiExample("Sent", value={"message": SEND_OTP_SUCCESS_MESSAGE})],
            ),
            400: OpenApiResponse(
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
        },
    )
    def send_otp(self, request):
        serializer = SendOTPSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        OTPService.send_verification_code(serializer.validated_data["email"])

        return Response({"message": SEND_OTP_SUCCESS_MESSAGE}, status=status.HTTP_200_OK)

    @extend_schema(request=VerifyOTPSerializer, tags=["Auth"])
    def verify_otp(self, request):
        serializer = VerifyOTPSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        valid, error = OTPService.verify(
            serializer.validated_data["email"], 
            serializer.validated_data["code"],
            purpose="verification"
        )
        if not valid:
            return Response({"error": error}, status=status.HTTP_400_BAD_REQUEST)
            
        User.objects.filter(email=serializer.validated_data["email"]).update(is_verified=True)
        return Response({"message": "OTP validation verified successfully."}, status=status.HTTP_200_OK)

    @extend_schema(request=ResendOTPSerializer, tags=["Auth"])
    def resend_otp(self, request):
        serializer = ResendOTPSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        OTPService.generate(serializer.validated_data["email"], purpose="verification")
        return Response({"message": "A fresh OTP code has been issued."}, status=status.HTTP_200_OK)

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
