import os
import uuid

from django.db import transaction

from config import file_storage
from ingestion.models import Payroll, PayrollUpload

from .file_parser import FileError, FileParser
from .standardiser import PayrollStandardiser
from .validation_service import ValidationService

MAX_ROWS = 20_000
MAX_ERRORS_SHOWN = 100
ROWS_INVALID_MESSAGE = "Some rows are invalid. Nothing was saved."


class UploadRejected(Exception):
    """The file or its rows failed a check. `body` is the 400 response; nothing was saved."""

    def __init__(self, body: dict):
        super().__init__(body.get("error", ""))
        self.body = body


class IngestionService:
    @staticmethod
    def upload_payroll(file, user) -> tuple[PayrollUpload, list[str]]:
        """Reads, standardises, and checks the whole file, keeps the original, then saves it all or nothing.

        Raises UploadRejected (400) or file_storage.StorageUnavailable (503); nothing is saved either way.
        """
        data = file.read()  # Stored exactly as checked: the parser reads the same bytes below.
        file.seek(0)
        try:
            headers, raw_rows = FileParser.parse(file)
        except FileError as exc:
            raise UploadRejected({"error": str(exc)})

        standardiser = PayrollStandardiser(headers)
        if standardiser.missing_columns:
            raise UploadRejected({
                "error": "Some required columns are missing. Check the column names in the first row.",
                "missing_columns": standardiser.missing_columns,
            })

        rows, errors = [], []
        for offset, cells in enumerate(raw_rows):
            row_number = offset + 2  # The header is spreadsheet row 1.
            values, row_errors = standardiser.row(cells)
            if values is None:
                continue
            rows.append((row_number, values))
            errors.extend({"row": row_number, "field": f, "error": e} for f, e in row_errors)
            if len(rows) > MAX_ROWS:
                raise UploadRejected({"error": f"The file has more than {MAX_ROWS:,} rows. Split it and upload each part."})

        if not rows:
            raise UploadRejected({"error": "The file has no payroll rows under the column names."})
        errors.extend(ValidationService.duplicates(rows))
        if errors:
            errors.sort(key=lambda e: e["row"])
            raise UploadRejected({
                "error": ROWS_INVALID_MESSAGE,
                "error_count": len(errors),
                "validation_errors": errors[:MAX_ERRORS_SHOWN],
            })

        upload_id = uuid.uuid4()
        extension = os.path.splitext(file.name or "")[1].lower()
        stored = file_storage.store_private(
            data, f"axiomvault/{user.organisation_id}/payroll/{upload_id}{extension}"
        )
        try:
            with transaction.atomic():
                upload = PayrollUpload.objects.create(
                    id=upload_id,
                    organisation=user.organisation,
                    uploaded_by=user,
                    file_name=(file.name or "")[:255],
                    row_count=len(rows),
                    source_public_id=stored["public_id"],
                    file_size=stored["bytes"],
                )
                Payroll.objects.bulk_create(
                    Payroll(organisation=user.organisation, upload=upload, **values) for _, values in rows
                )
        except Exception:
            # The rows were not saved, so the stored file must not linger as evidence of nothing.
            file_storage.delete(stored["public_id"])
            raise
        return upload, standardiser.ignored_columns
