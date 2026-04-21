from django.contrib import admin
from django.urls import path, include

from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularSwaggerView,
    SpectacularRedocView,
)

urlpatterns = [
    path("admin/", admin.site.urls),

    # Auth endpoints
    path("api/v1/auth/", include("accounts.urls")),

    # 🔥 Schema
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),

    # 🔥 Swagger UI
    path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger-ui"),

    # 🔥 Redoc
    path("api/redoc/", SpectacularRedocView.as_view(url_name="schema"), name="redoc"),
]