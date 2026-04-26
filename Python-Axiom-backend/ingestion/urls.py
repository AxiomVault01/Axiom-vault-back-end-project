from rest_framework.routers import DefaultRouter
from .viewsets import IngestionViewSet

router = DefaultRouter()
router.register("ingestion", IngestionViewSet, basename="ingestion")

urlpatterns = router.urls