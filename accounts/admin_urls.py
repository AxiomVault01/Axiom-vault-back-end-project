from django.urls import path
from rest_framework.routers import SimpleRouter

from .admin_viewsets import AdminAuthViewSet, AdminLogoutViewSet, OrganisationAdminViewSet

router = SimpleRouter()
router.register("organisations", OrganisationAdminViewSet, basename="admin-organisation")

urlpatterns = [
    path("auth/login/", AdminAuthViewSet.as_view({"post": "login"}), name="admin-login"),
    path("auth/logout/", AdminLogoutViewSet.as_view({"post": "logout"}), name="admin-logout"),
    *router.urls,
]
