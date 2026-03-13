from rest_framework.routers import DefaultRouter
from .viewsets import FraudResultViewSet, AlertViewSet

router = DefaultRouter()

router.register("results", FraudResultViewSet, basename="fraud-results")
router.register("alerts", AlertViewSet, basename="alerts")

urlpatterns = router.urls