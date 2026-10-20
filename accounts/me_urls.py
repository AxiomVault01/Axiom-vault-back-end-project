from rest_framework.routers import SimpleRouter

from .me_viewsets import MeViewSet

router = SimpleRouter()
router.register("", MeViewSet, basename="me")

urlpatterns = router.urls
