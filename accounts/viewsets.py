from rest_framework import viewsets, status
from rest_framework.response import Response
from drf_spectacular.utils import extend_schema
from django.contrib.auth import get_user_model
from .services import OTPService, AuthService

# 👇 ADD THIS EXACT IMPORT AT THE TOP OF YOUR VIEWS.PY FILE
from django.conf import settings


# from .services import OTPService, AuthService  
from .serializers import (
    SendOTPSerializer,
    VerifyOTPSerializer,
    ResendOTPSerializer,
    SignupSerializer,
    LoginSerializer,
    ResetPasswordSerializer
)

User = get_user_model()

class AuthViewSet(viewsets.ViewSet):

    serializer_class = SendOTPSerializer 
    """Unified ViewSet routing requests straight to dedicated service engines."""

    @extend_schema(request=SendOTPSerializer, tags=["Auth"])
    def send_otp(self, request):
        serializer = SendOTPSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        email = serializer.validated_data["email"]

        if settings.DEBUG:
            # 🧪 Running locally? Execute it directly and synchronously
            OTPService.generate(email, purpose="verification")
        else:
            # 🚀 Running live on Render? Dispatch it safely to your Celery background tasks
            # Replace 'generate_otp_task' with the exact name of your project's Celery task
            generate_otp_task.delay(email, purpose="verification")

        return Response({"message": "OTP verification code transmitted successfully."}, status=status.HTTP_200_OK)

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
