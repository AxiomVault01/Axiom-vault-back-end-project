from django.urls import path

from .admin_viewsets import AdminAuthViewSet, AdminLogoutViewSet

urlpatterns = [
    path("auth/login/", AdminAuthViewSet.as_view({"post": "login"}), name="admin-login"),
    path("auth/logout/", AdminLogoutViewSet.as_view({"post": "logout"}), name="admin-logout"),
]
