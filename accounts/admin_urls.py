from django.urls import path
from rest_framework.routers import SimpleRouter

from .admin_viewsets import (
    AdminAuthViewSet,
    AdminLogoutViewSet,
    AdminUserViewSet,
    OrganisationAdminViewSet,
    RolesViewSet,
    StaffAdminViewSet,
)

router = SimpleRouter()
router.register("organisations", OrganisationAdminViewSet, basename="admin-organisation")
router.register("users", AdminUserViewSet, basename="admin-user")
router.register("staff", StaffAdminViewSet, basename="admin-staff")
router.register("roles", RolesViewSet, basename="admin-roles")

urlpatterns = [
    path("auth/login/", AdminAuthViewSet.as_view({"post": "login"}), name="admin-login"),
    path("auth/logout/", AdminLogoutViewSet.as_view({"post": "logout"}), name="admin-logout"),
    *router.urls,
]
