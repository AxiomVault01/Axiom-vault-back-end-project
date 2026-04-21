from django.urls import path
from .viewsets import AuthViewSet

auth = AuthViewSet.as_view

urlpatterns = [
    path("signup/", auth({"post": "signup"})),
    path("verify-otp/", auth({"post": "verify_otp"})),
    path("login/", auth({"post": "login"})),
    path("forgot-password/", auth({"post": "forgot_password"})),
    path("reset-password/", auth({"post": "reset_password"})),
    path("logout/", auth({"post": "logout"})),
]