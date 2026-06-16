# from django.urls import path
# from .viewsets import AuthViewSet 

# auth = AuthViewSet.as_view

# urlpatterns = [
#     path("signup/", auth({"post": "signup"})),
#     # path("verify-otp/", auth({"post": "verify_otp"})),
#     path("login/", auth({"post": "login"})),
#     path("forgot-password/", auth({"post": "forgot_password"})),
#     path("reset-password/", auth({"post": "reset_password"})),
#     path("logout/", auth({"post": "logout"})),

#     path("send-otp/", auth({"post": "send_otp"})),
#     path("verify-otp/", auth({"post": "verify_otp"})),
#     path("resend-otp/", auth({"post": "resend_otp"})),
# ]





from django.urls import path
from .viewsets import AuthViewSet 

# Bind the ViewSet actions to individual HTTP POST endpoints
auth = AuthViewSet.as_view

urlpatterns = [
    # Account Management Endpoints
    path("signup/", auth({"post": "signup"}), name="auth-signup"),
    path("login/", auth({"post": "login"}), name="auth-login"),
    path("logout/", auth({"post": "logout"}), name="auth-logout"),
    
    # Password Recovery Endpoints
    path("forgot-password/", auth({"post": "forgot_password"}), name="auth-forgot-password"),
    path("reset-password/", auth({"post": "reset_password"}), name="auth-reset-password"),

    # Direct Multi-Factor / OTP Flow Endpoints
    path("send-otp/", auth({"post": "send_otp"}), name="auth-send-otp"),
    path("verify-otp/", auth({"post": "verify_otp"}), name="auth-verify-otp"),
    path("resend-otp/", auth({"post": "resend_otp"}), name="auth-resend-otp"),
]
