from django.urls import path

from .viewsets import IngestionViewSet

urlpatterns = [
    path("upload-payroll/", IngestionViewSet.as_view({"post": "upload_payroll"}), name="ingestion-upload-payroll"),
]
