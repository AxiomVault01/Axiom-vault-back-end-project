from rest_framework import serializers
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password

User = get_user_model()

class SendOTPSerializer(serializers.Serializer):
    email = serializers.EmailField(required=True)

class VerifyOTPSerializer(serializers.Serializer):
    email = serializers.EmailField(required=True)
    code = serializers.CharField(max_length=6, required=True)

class ResendOTPSerializer(serializers.Serializer):
    email = serializers.EmailField(required=True)

class SignupSerializer(serializers.Serializer):
    full_name = serializers.CharField(max_length=255, required=True)
    email = serializers.EmailField(required=True)
    organization = serializers.CharField(max_length=255, required=True)
    
    # Dropdowns pulling directly from the available model constraints
    role = serializers.ChoiceField(choices=User.ROLE_CHOICES, required=True)
    department = serializers.ChoiceField(
        choices=[
            ("operations", "Operations"), 
            ("compliance", "Compliance"), 
            ("risk", "Risk Assessment"), 
            ("management", "Management")
        ], 
        required=True
    )
    
    password = serializers.CharField(write_only=True, required=True, validators=[validate_password])
    re_enter_password = serializers.CharField(write_only=True, required=True)

    def validate_email(self, value):
        if User.objects.filter(email=value).exists():
            raise serializers.ValidationError("An account with this email address already exists.")
        return value

    def validate(self, data):
        if data['password'] != data['re_enter_password']:
            raise serializers.ValidationError({"re_enter_password": "Passwords do not match."})
        return data

class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField(required=True)
    password = serializers.CharField(write_only=True, required=True)

class ResetPasswordSerializer(serializers.Serializer):
    email = serializers.EmailField(required=True)
    otp_code = serializers.CharField(max_length=6, required=True)
    new_password = serializers.CharField(write_only=True, required=True, validators=[validate_password])
