from django.urls import path
from rest_framework.routers import SimpleRouter

from .me_viewsets import MeViewSet

router = SimpleRouter()
router.register("", MeViewSet, basename="me")

urlpatterns = [
    path("", MeViewSet.as_view({"get": "retrieve_profile", "patch": "update_profile"}), name="me-profile"),
    *router.urls,
]
