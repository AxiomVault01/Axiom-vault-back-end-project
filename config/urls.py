from django.contrib import admin
from django.urls import path, include

from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularSwaggerView,
    SpectacularRedocView,
)

urlpatterns = [
    path("admin/", admin.site.urls),

    # Authentication
    path("api/v1/auth/", include("accounts.urls")),

    # Team management (organisation super admin)
    path("api/v1/team/", include("accounts.team_urls")),

    # Fraud Detection
    path("api/v1/fraud/", include("fraud.urls")),

    # Dashboard and Analytics screens
    path("api/v1/dashboard/", include("fraud.dashboard_urls")),

    # Ingestion
    path("api/v1/ingestion/", include("ingestion.urls")),

    # General file upload (any attachment, e.g. case evidence)
    path("api/v1/files/", include("ingestion.file_urls")),

    # OpenAPI Schema
    path(
        "api/schema/",
        SpectacularAPIView.as_view(),
        name="schema"
    ),

    # Swagger UI
    path(
        "api/docs/",
        SpectacularSwaggerView.as_view(
            url_name="schema"
        ),
        name="swagger-ui",
    ),

    # ReDoc
    path(
        "api/redoc/",
        SpectacularRedocView.as_view(
            url_name="schema"
        ),
        name="redoc",
    ),
]