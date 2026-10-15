from rest_framework.routers import SimpleRouter

from .dashboard import DashboardViewSet

router = SimpleRouter()
router.register("", DashboardViewSet, basename="dashboard")

urlpatterns = router.urls
