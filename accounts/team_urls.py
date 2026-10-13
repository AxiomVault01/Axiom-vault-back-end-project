from rest_framework.routers import SimpleRouter

from .team_viewsets import TeamMemberViewSet

router = SimpleRouter()
router.register("members", TeamMemberViewSet, basename="team-member")

urlpatterns = router.urls
