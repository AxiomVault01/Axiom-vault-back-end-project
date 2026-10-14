from django.urls import path

from .viewsets import IngestionViewSet

urlpatterns = [
    path("upload-payroll/", IngestionViewSet.as_view({"post": "upload_payroll"}), name="ingestion-upload-payroll"),
    path("upload-vendors/", IngestionViewSet.as_view({"post": "upload_vendors"}), name="ingestion-upload-vendors"),
    path("upload-payments/", IngestionViewSet.as_view({"post": "upload_payments"}), name="ingestion-upload-payments"),
]
