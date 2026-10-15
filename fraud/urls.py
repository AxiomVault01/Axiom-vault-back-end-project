from rest_framework.routers import SimpleRouter

from .case_viewsets import CaseViewSet
from .viewsets import AlertViewSet

router = SimpleRouter()
router.register("alerts", AlertViewSet, basename="alert")
router.register("cases", CaseViewSet, basename="case")

urlpatterns = router.urls
