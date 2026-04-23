from rest_framework import status
from rest_framework.viewsets import GenericViewSet
from rest_framework.response import Response
from rest_framework.decorators import action
from django.shortcuts import get_object_or_404

# ✅ Use drf-spectacular instead of drf_yasg
from drf_spectacular.utils import extend_schema

from .models import User
from .serializers import (
    SignupSerializer,
    VerifyOTPSerializer,
    LoginSerializer,
    ForgotPasswordSerializer,
    ResetPasswordSerializer,
)
from .services.otp_service import OTPService
from .services.auth_service import AuthService


class AuthViewSet(GenericViewSet):

    @extend_schema(request=SignupSerializer, tags=["Auth"])
    @action(detail=False, methods=["post"])
    def signup(self, request):
        serializer = SignupSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        user = serializer.save()

        # Generate OTP
        OTPService.generate(user, "signup")

        return Response(
            {"message": "Signup successful. OTP sent."},
            status=status.HTTP_201_CREATED
        )

    @extend_schema(request=VerifyOTPSerializer, tags=["Auth"])
    @action(detail=False, methods=["post"], url_path="verify-otp")
    def verify_otp(self, request):
        serializer = VerifyOTPSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        user = get_object_or_404(User, email=serializer.validated_data["email"])

        valid, error = OTPService.verify(
            user,
            serializer.validated_data["code"],
            "signup"
        )

        if not valid:
            return Response(
                {"error": error},
                status=status.HTTP_400_BAD_REQUEST
            )

        user.is_verified = True
        user.save(update_fields=["is_verified"])

        return Response(
            {"message": "Account verified"},
            status=status.HTTP_200_OK
        )

    @extend_schema(request=LoginSerializer, tags=["Auth"])
    @action(detail=False, methods=["post"])
    def login(self, request):
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        tokens, error = AuthService.login(
            serializer.validated_data["email"],
            serializer.validated_data["password"],
        )

        if error:
            return Response(
                {"error": error},
                status=status.HTTP_400_BAD_REQUEST
            )

        return Response(tokens, status=status.HTTP_200_OK)

    @extend_schema(request=ForgotPasswordSerializer, tags=["Auth"])
    @action(detail=False, methods=["post"], url_path="forgot-password")
    def forgot_password(self, request):
        serializer = ForgotPasswordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        user = get_object_or_404(User, email=serializer.validated_data["email"])

        OTPService.generate(user, "reset")

        return Response(
            {"message": "OTP sent for password reset"},
            status=status.HTTP_200_OK
        )

    @extend_schema(request=ResetPasswordSerializer, tags=["Auth"])
    @action(detail=False, methods=["post"], url_path="reset-password")
    def reset_password(self, request):
        serializer = ResetPasswordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        data = serializer.validated_data
        user = get_object_or_404(User, email=data["email"])

        valid, error = OTPService.verify(user, data["code"], "reset")

        if not valid:
            return Response(
                {"error": error},
                status=status.HTTP_400_BAD_REQUEST
            )

        user.set_password(data["new_password"])
        user.save(update_fields=["password"])

        return Response(
            {"message": "Password reset successful"},
            status=status.HTTP_200_OK
        )

    @extend_schema(tags=["Auth"])
    @action(detail=False, methods=["post"])
    def logout(self, request):
        refresh_token = request.data.get("refresh")

        if not refresh_token:
            return Response(
                {"error": "Refresh token required"},
                status=status.HTTP_400_BAD_REQUEST
            )

        success = AuthService.logout(refresh_token)

        if not success:
            return Response(
                {"error": "Invalid or expired token"},
                status=status.HTTP_400_BAD_REQUEST
            )

        return Response(
            {"message": "Logout successful"},
            status=status.HTTP_200_OK
        )