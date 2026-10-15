from rest_framework.routers import SimpleRouter

from .file_viewsets import StoredFileViewSet

router = SimpleRouter()
router.register("", StoredFileViewSet, basename="file")

urlpatterns = router.urls
