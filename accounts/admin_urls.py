from django.urls import path

from .admin_viewsets import AdminAuthViewSet

urlpatterns = [
    path("auth/login/", AdminAuthViewSet.as_view({"post": "login"}), name="admin-login"),
]
