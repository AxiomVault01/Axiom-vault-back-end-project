from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response

from drf_spectacular.utils import extend_schema

from .services.file_parser import FileParser
from .services.validation_service import ValidationService
from .services.ingestion_service import IngestionService


class IngestionViewSet(viewsets.ViewSet):

    @extend_schema(tags=["Ingestion"])
    @action(detail=False, methods=["post"], url_path="upload-payroll")
    def upload_payroll(self, request):

        file = request.FILES.get("file")

        if not file:
            return Response(
                {"error": "File is required"},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            rows = FileParser.parse(file)
        except Exception as e:
            return Response({"error": str(e)}, status=400)

        # ✅ Validate
        errors = ValidationService.validate(rows)

        if errors:
            return Response(
                {"validation_errors": errors},
                status=status.HTTP_400_BAD_REQUEST
            )

        # ✅ Save
        IngestionService.save_payroll(rows)

        return Response(
            {"message": "Payroll uploaded successfully"},
            status=status.HTTP_201_CREATED
        )