import io
from datetime import date, datetime
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from openpyxl import Workbook
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from accounts.models import AuditLog, Organisation
from ingestion.models import DataUpload, Payment, Payroll, Vendor
from ingestion.services.file_parser import FileError, FileParser
from ingestion.services.standardiser import PAYMENT_SCHEMA, PAYROLL_SCHEMA, VENDOR_SCHEMA, Standardiser
from ingestion.services.validation_service import ValidationService
# Imported before the autouse fake replaces them, so the helper itself can be tested.
from config.file_storage import StorageUnavailable, delete as real_delete, store_private as real_store_private

User = get_user_model()
UPLOAD_URL = "/api/v1/ingestion/upload-payroll/"
HEADERS = [
    "employee_id", "employee_name", "department", "salary_basic", "net_salary",
    "account_number", "pay_period", "payment_date", "role", "approver_id",
]
GOOD_ROW = ["EMP001", "Ada Obi", "Operations", "250,000.00", "230000", "0123456789", "2026-09", "28/09/2026", "Payroll Analyst", "APR-01"]


def csv_file(rows, headers=HEADERS, name="payroll.csv", encoding="utf-8"):
    lines = [",".join(headers)] + [",".join(f'"{c}"' for c in row) for row in rows]
    return SimpleUploadedFile(name, ("\n".join(lines) + "\n").encode(encoding), content_type="text/csv")


def xlsx_file(rows, headers=HEADERS, name="payroll.xlsx"):
    book = Workbook()
    sheet = book.active
    sheet.append(headers)
    for row in rows:
        sheet.append(row)
    buffer = io.BytesIO()
    book.save(buffer)
    return SimpleUploadedFile(name, buffer.getvalue())


def standardise(rows, headers=HEADERS):
    standardiser = Standardiser(PAYROLL_SCHEMA, headers)
    return standardiser, [standardiser.row(row) for row in rows]


# ---- 7c: reading the file ----


def test_csv_cells_stay_text_so_leading_zeros_survive():
    headers, rows = FileParser.parse(csv_file([GOOD_ROW]))

    assert headers == HEADERS
    assert rows == [GOOD_ROW]


def test_csv_with_a_bom_or_windows_encoding_is_read():
    bom = SimpleUploadedFile("p.csv", "﻿employee_id,employee_name\nEMP1,Adé\n".encode("utf-8"))
    ansi = SimpleUploadedFile("p.csv", "employee_id,employee_name\nEMP1,Adé\n".encode("cp1252"))

    assert FileParser.parse(bom) == (["employee_id", "employee_name"], [["EMP1", "Adé"]])
    assert FileParser.parse(ansi) == (["employee_id", "employee_name"], [["EMP1", "Adé"]])


def test_xlsx_numbers_and_dates_come_back_usable():
    headers, rows = FileParser.parse(xlsx_file([["EMP001", "Ada", "Ops", 250000.0, 230000.5, "0123456789", "2026-09", datetime(2026, 9, 28), "Analyst", "APR-01"]]))

    assert headers == HEADERS
    assert rows[0][3] == "250000" and rows[0][4] == "230000.5"
    assert rows[0][5] == "0123456789"
    assert rows[0][7] == datetime(2026, 9, 28)


@pytest.mark.parametrize(
    "name, message",
    [("payroll.xls", "Old .xls files"), ("payroll.pdf", "Upload a .csv or .xlsx file."), ("payroll", "Upload a .csv")],
)
def test_unsupported_file_types_are_refused(name, message):
    with pytest.raises(FileError) as excinfo:
        FileParser.parse(SimpleUploadedFile(name, b"x"))
    assert message in str(excinfo.value)


def test_empty_or_broken_files_are_refused():
    with pytest.raises(FileError):
        FileParser.parse(SimpleUploadedFile("p.csv", b""))
    with pytest.raises(FileError):
        FileParser.parse(SimpleUploadedFile("p.xlsx", b"not a workbook"))


# ---- 7c: standard format ----


def test_aliases_and_letter_case_map_to_standard_fields():
    standardiser = Standardiser(
        PAYROLL_SCHEMA,
        ["Staff ID", "Full Name", "DEPT", "Basic Salary", "Net-Pay", "Account No", "Period", "Pay Date", "Remarks", "Branch", "Position", "Approver ID"]
    )

    assert standardiser.missing_columns == []
    assert standardiser.columns["net_salary"] == 4
    assert standardiser.columns["location"] == 9
    assert standardiser.ignored_columns == ["Remarks"]


def test_missing_required_columns_are_listed():
    standardiser = Standardiser(PAYROLL_SCHEMA, ["employee_id", "employee_name"])

    assert standardiser.missing_columns == ["department", "role", "account_number", "net_salary", "payment_date", "pay_period", "approver_id"]


def test_a_good_row_becomes_typed_values():
    _, [(values, errors)] = standardise([GOOD_ROW])

    assert errors == []
    assert values["salary_basic"] == Decimal("250000.00")
    assert values["account_number"] == "0123456789"
    assert values["payment_date"] == date(2026, 9, 28)


def test_blank_rows_are_skipped():
    _, [(values, errors)] = standardise([["", " ", "", "", "", "", "", "", "", ""]])

    assert values is None and errors == []


@pytest.mark.parametrize(
    "index, value, expected",
    [
        (3, "₦250,000", Decimal("250000")),
        (3, "NGN 1,000.50", Decimal("1000.50")),
        (5, "0123-456 789", "0123456789"),
        (7, "2026-09-28", date(2026, 9, 28)),
        (7, "28-09-2026", date(2026, 9, 28)),
        (7, "03/04/2026", date(2026, 4, 3)),
    ],
)
def test_accepted_value_formats(index, value, expected):
    row = list(GOOD_ROW)
    row[index] = value
    _, [(values, errors)] = standardise([row])

    assert errors == []
    assert values[HEADERS[index]] == expected


@pytest.mark.parametrize(
    "index, value, message",
    [
        (0, "", "This value is required."),
        (3, "abc", "Enter an amount"),
        (4, "-1", "cannot be negative"),
        (4, "10.555", "at most 2 decimal places"),
        (4, "NaN", "Enter an amount"),
        (4, "99999999999", "too large"),
        (5, "01234ABC", "digits only"),
        (5, "1" * 21, "digits only"),
        (6, "x" * 21, "at most 20 characters"),
        (7, "Sept 28", "Use a date like"),
        (7, "2026-02-30", "Use a date like"),
    ],
)
def test_invalid_values_are_reported_per_field(index, value, message):
    row = list(GOOD_ROW)
    row[index] = value
    _, [(_, errors)] = standardise([row])

    assert len(errors) == 1
    assert errors[0][0] == HEADERS[index]
    assert message in errors[0][1]


def test_optional_fields_default_and_check():
    headers = HEADERS + ["allowances", "deductions", "attendance_days", "leave_days"]
    _, [(blank, blank_errors), (bad, bad_errors)] = standardise(
        [GOOD_ROW + ["", "", "", ""], GOOD_ROW + ["-5", "1", "2.5", "-1"]], headers
    )

    assert blank_errors == []
    assert blank["allowances"] == Decimal("0") and blank["attendance_days"] is None
    assert sorted(f for f, _ in bad_errors) == ["allowances", "attendance_days", "leave_days"]


def test_duplicates_report_the_first_row():
    rows = [(2, {"employee_id": "E1", "account_number": "1", "pay_period": "2026-09"})] * 2 + [
        (4, {"employee_id": "E1", "account_number": "1", "pay_period": "2026-10"})
    ]
    rows[1] = (3, rows[1][1])

    errors = ValidationService.duplicates(rows, PAYROLL_SCHEMA.duplicate_key, PAYROLL_SCHEMA.duplicate_message)

    assert errors == [{"row": 3, "field": "employee_id", "error": PAYROLL_SCHEMA.duplicate_message.format(first=2)}]


# ---- 7b: the upload endpoint ----


@pytest.fixture
def uploader(db):
    organisation = Organisation.objects.create(name="Ikeja Coop")
    user = User.objects.create_user(
        username="owner", email="owner@ikejacoop.ng", password="Str0ng-Passw0rd!", full_name="Owner",
        organisation=organisation, account_type="super_admin", is_verified=True,
    )
    return user


def client_for(user):
    client = APIClient(REMOTE_ADDR="203.0.113.9")
    client.credentials(HTTP_AUTHORIZATION="Bearer " + str(AccessToken.for_user(user)))
    return client


def test_csv_upload_saves_every_row_for_the_organisation(uploader):
    second = list(GOOD_ROW)
    second[0] = "EMP002"
    response = client_for(uploader).post(UPLOAD_URL, {"file": csv_file([GOOD_ROW, second])}, format="multipart")

    assert response.status_code == 201
    body = response.json()
    assert body["upload"]["row_count"] == 2 and body["upload"]["file_name"] == "payroll.csv"
    assert body["ignored_columns"] == []
    upload = DataUpload.objects.get()
    assert upload.organisation == uploader.organisation and upload.uploaded_by == uploader
    assert list(Payroll.objects.values_list("employee_id", flat=True).order_by("employee_id")) == ["EMP001", "EMP002"]
    assert set(Payroll.objects.values_list("organisation", "upload")) == {(uploader.organisation_id, upload.id)}
    assert Payroll.objects.get(employee_id="EMP001").account_number == "0123456789"
    audit = AuditLog.objects.get(action=AuditLog.PAYROLL_UPLOADED)
    assert audit.actor == uploader and audit.target_type == "dataupload" and audit.target_id == str(upload.id)
    assert audit.details == {"file_name": "payroll.csv", "row_count": 2}


def test_xlsx_upload_with_aliases_and_extra_columns(uploader):
    headers = ["Staff ID", "Name", "Dept", "Basic", "Net Pay", "Account No", "Period", "Pay Date", "Remarks", "Job Title", "Approved By"]
    row = ["EMP9", "Bola", "Finance", 300000, 280000, "0011223344", "2026-09", datetime(2026, 9, 30), "ok", "Accountant", "APR-02"]

    response = client_for(uploader).post(UPLOAD_URL, {"file": xlsx_file([row], headers)}, format="multipart")

    assert response.status_code == 201
    assert response.json()["ignored_columns"] == ["Remarks"]
    saved = Payroll.objects.get()
    assert saved.payment_date == date(2026, 9, 30) and saved.net_salary == Decimal("280000")


def test_one_bad_row_saves_nothing(uploader):
    bad = list(GOOD_ROW)
    bad[0], bad[4] = "EMP002", "-10"

    response = client_for(uploader).post(UPLOAD_URL, {"file": csv_file([GOOD_ROW, bad, GOOD_ROW])}, format="multipart")

    assert response.status_code == 400
    body = response.json()
    assert body["error"] == "Some rows are invalid. Nothing was saved."
    assert body["error_count"] == 2
    assert body["validation_errors"][0] == {"row": 3, "field": "net_salary", "error": "The amount cannot be negative."}
    assert body["validation_errors"][1]["row"] == 4
    assert DataUpload.objects.count() == 0 and Payroll.objects.count() == 0
    assert not AuditLog.objects.filter(action=AuditLog.PAYROLL_UPLOADED).exists()


def test_only_the_first_hundred_errors_are_returned(uploader):
    rows = [[f"E{i}", "A", "B", "x", "1", "1", "P", "2026-01-01", "Clerk", "APR-01"] for i in range(150)]

    body = client_for(uploader).post(UPLOAD_URL, {"file": csv_file(rows)}, format="multipart").json()

    assert body["error_count"] == 150 and len(body["validation_errors"]) == 100


@pytest.mark.parametrize(
    "file, error_start",
    [
        (lambda: csv_file([GOOD_ROW], headers=["employee_id", "employee_name"]), "Some required columns are missing"),
        (lambda: csv_file([]), "The file has no rows"),
        (lambda: SimpleUploadedFile("p.txt", b"hello"), "Upload a .csv or .xlsx file."),
    ],
)
def test_file_level_problems_get_400(uploader, file, error_start):
    response = client_for(uploader).post(UPLOAD_URL, {"file": file()}, format="multipart")

    assert response.status_code == 400
    assert response.json()["error"].startswith(error_start)
    assert Payroll.objects.count() == 0


def test_missing_columns_are_named(uploader):
    body = client_for(uploader).post(
        UPLOAD_URL, {"file": csv_file([GOOD_ROW[:2]], headers=["employee_id", "employee_name"])}, format="multipart"
    ).json()

    assert body["missing_columns"] == ["department", "role", "account_number", "net_salary", "payment_date", "pay_period", "approver_id"]


def test_no_file_gets_400(uploader):
    response = client_for(uploader).post(UPLOAD_URL, {}, format="multipart")

    assert response.status_code == 400
    assert "file" in response.json()


def test_too_many_rows_gets_400(uploader):
    with patch("ingestion.services.ingestion_service.MAX_ROWS", 2):
        rows = [[f"E{i}"] + GOOD_ROW[1:] for i in range(3)]
        response = client_for(uploader).post(UPLOAD_URL, {"file": csv_file(rows)}, format="multipart")

    assert response.status_code == 400
    assert "more than 2 rows" in response.json()["error"]


def test_too_large_file_gets_413(uploader):
    with patch("ingestion.viewsets.MAX_FILE_BYTES", 10):
        response = client_for(uploader).post(UPLOAD_URL, {"file": csv_file([GOOD_ROW])}, format="multipart")

    assert response.status_code == 413
    assert response.json() == {"error": "The file is larger than 10 MB."}


def test_upload_needs_a_token(db):
    response = APIClient().post(UPLOAD_URL, {"file": csv_file([GOOD_ROW])}, format="multipart")

    assert response.status_code == 401


def test_account_without_an_organisation_cannot_upload(db):
    staff = User.objects.create_user(username="ops", email="ops@example.com", password="Str0ng-Passw0rd!", is_staff=True)

    response = client_for(staff).post(UPLOAD_URL, {"file": csv_file([GOOD_ROW])}, format="multipart")

    assert response.status_code == 403
    assert response.json() == {"error": "Only organisation accounts can upload files."}


def test_team_member_may_upload_but_not_on_a_temporary_password(uploader):
    member = User.objects.create_user(
        username="ada", email="ada@ikejacoop.ng", password="Str0ng-Passw0rd!", organisation=uploader.organisation,
        account_type="team_member", is_verified=True, must_change_password=True,
    )

    blocked = client_for(member).post(UPLOAD_URL, {"file": csv_file([GOOD_ROW])}, format="multipart")
    User.objects.filter(pk=member.pk).update(must_change_password=False)
    member.refresh_from_db()
    allowed = client_for(member).post(UPLOAD_URL, {"file": csv_file([GOOD_ROW])}, format="multipart")

    assert blocked.status_code == 403 and blocked.json()["code"] == "password_change_required"
    assert allowed.status_code == 201


def test_the_same_employee_in_two_uploads_is_allowed(uploader):
    client = client_for(uploader)

    first = client.post(UPLOAD_URL, {"file": csv_file([GOOD_ROW])}, format="multipart")
    second = client.post(UPLOAD_URL, {"file": csv_file([GOOD_ROW])}, format="multipart")

    assert first.status_code == second.status_code == 201
    assert Payroll.objects.count() == 2


# ---- 7d: keep the source file ----


def test_store_private_uploads_a_private_raw_file(monkeypatch):
    monkeypatch.setenv("CLOUDINARY_URL", "cloudinary://key:secret@demo")
    with patch("cloudinary.uploader.upload", return_value={"public_id": "axiomvault/o/payroll/u.csv", "bytes": 5}) as upload:
        stored = real_store_private(b"hello", "axiomvault/o/payroll/u.csv")

    assert stored == {"public_id": "axiomvault/o/payroll/u.csv", "bytes": 5}
    _, kwargs = upload.call_args
    assert upload.call_args.args[0] == b"hello"
    assert kwargs == {
        "public_id": "axiomvault/o/payroll/u.csv", "resource_type": "raw", "type": "authenticated", "overwrite": False
    }


def test_store_private_needs_configuration(monkeypatch):
    monkeypatch.delenv("CLOUDINARY_URL", raising=False)

    with pytest.raises(StorageUnavailable):
        real_store_private(b"hello", "x")


def test_store_private_turns_cloudinary_errors_into_storage_unavailable(monkeypatch):
    monkeypatch.setenv("CLOUDINARY_URL", "cloudinary://key:secret@demo")
    with patch("cloudinary.uploader.upload", side_effect=RuntimeError("boom")):
        with pytest.raises(StorageUnavailable):
            real_store_private(b"hello", "x")


def test_delete_never_raises(monkeypatch):
    monkeypatch.setenv("CLOUDINARY_URL", "cloudinary://key:secret@demo")
    with patch("cloudinary.uploader.destroy", side_effect=RuntimeError("boom")) as destroy:
        real_delete("axiomvault/o/payroll/u.csv")

    assert destroy.call_args.kwargs == {"resource_type": "raw", "type": "authenticated", "invalidate": True}


def test_upload_keeps_the_exact_original_file(uploader, fake_storage):
    sent = csv_file([GOOD_ROW])
    raw = sent.read()
    sent.seek(0)

    body = client_for(uploader).post(UPLOAD_URL, {"file": sent}, format="multipart").json()

    upload = DataUpload.objects.get()
    expected_id = f"axiomvault/{uploader.organisation_id}/payroll/{upload.id}.csv"
    assert upload.source_public_id == expected_id
    assert fake_storage.files[expected_id] == raw
    assert upload.file_size == len(raw) == body["upload"]["file_size"]
    assert "source_public_id" not in body["upload"]


def test_rejected_files_are_never_stored(uploader, fake_storage):
    bad = list(GOOD_ROW)
    bad[4] = "-1"

    client_for(uploader).post(UPLOAD_URL, {"file": csv_file([bad])}, format="multipart")

    assert fake_storage.files == {}


def test_storage_failure_gives_503_and_saves_nothing(uploader, monkeypatch):
    from config import file_storage

    def broken(data, public_id):
        raise StorageUnavailable("down")

    monkeypatch.setattr(file_storage, "store_private", broken)

    response = client_for(uploader).post(UPLOAD_URL, {"file": csv_file([GOOD_ROW])}, format="multipart")

    assert response.status_code == 503
    assert response.json() == {
        "error": "The file could not be stored right now. Nothing was saved. Try again in a few minutes."
    }
    assert DataUpload.objects.count() == 0 and Payroll.objects.count() == 0


def test_a_database_failure_after_storing_removes_the_stored_file(uploader, fake_storage):
    from ingestion.services.ingestion_service import IngestionService

    with patch("ingestion.models.Payroll.objects.bulk_create", side_effect=RuntimeError("db down")):
        with pytest.raises(RuntimeError):
            IngestionService.upload(csv_file([GOOD_ROW]), uploader, PAYROLL_SCHEMA, Payroll)

    assert fake_storage.files == {}
    assert len(fake_storage.deleted) == 1
    assert DataUpload.objects.count() == 0


def test_a_malformed_cloudinary_url_gives_storage_unavailable(monkeypatch):
    monkeypatch.setenv("CLOUDINARY_URL", "CLOUDINARY_URL=cloudinary://key:secret@demo")
    with patch("cloudinary.uploader.upload", side_effect=ValueError("Invalid CLOUDINARY_URL scheme")):
        with pytest.raises(StorageUnavailable):
            real_store_private(b"hello", "x")


# ---- 7e: vendor and payment uploads ----

VENDORS_URL = "/api/v1/ingestion/upload-vendors/"
PAYMENTS_URL = "/api/v1/ingestion/upload-payments/"
VENDOR_HEADERS = ["vendor_id", "vendor_name", "account_number", "bank_name", "registration_number", "address", "phone", "email", "date_registered"]
VENDOR_ROW = ["VND-8847", "Acme Supplies", "0012345678", "GTBank", "RC-123456", "12 Allen Ave, Ikeja", "+234 803-000-1111", "Accounts@Acme.ng", "15/01/2024"]
PAYMENT_HEADERS = ["transaction_id", "vendor_id", "invoice_number", "amount", "payment_date", "approver", "cost_centre", "department", "description"]
PAYMENT_ROW = ["TXN-90214", "VND-8847", "INV-2024-8847", "₦42,350.00", "2026-05-05", "John Doe", "CC-01", "Procurement", "Office supplies"]


def test_vendor_schema_aliases_and_values():
    standardiser = Standardiser(VENDOR_SCHEMA, ["Supplier ID", "Supplier Name", "Account No", "RC Number", "Email Address", "Notes"])

    assert standardiser.missing_columns == []
    assert standardiser.ignored_columns == ["Notes"]
    values, errors = Standardiser(VENDOR_SCHEMA, VENDOR_HEADERS).row(VENDOR_ROW)
    assert errors == []
    assert values["phone"] == "+2348030001111" and values["email"] == "accounts@acme.ng"
    assert values["account_number"] == "0012345678" and values["date_registered"] == date(2024, 1, 15)


def test_vendor_optional_contact_fields_may_be_blank_but_must_be_valid():
    blank = list(VENDOR_ROW[:3]) + ["", "", "", "", "", ""]
    bad = list(VENDOR_ROW)
    bad[6], bad[7], bad[8] = "12", "not-an-email", "31/31/2024"

    blank_values, blank_errors = Standardiser(VENDOR_SCHEMA, VENDOR_HEADERS).row(blank)
    _, bad_errors = Standardiser(VENDOR_SCHEMA, VENDOR_HEADERS).row(bad)

    assert blank_errors == [] and blank_values["email"] == "" and blank_values["date_registered"] is None
    assert sorted(f for f, _ in bad_errors) == ["date_registered", "email", "phone"]


def test_vendor_and_payment_required_columns():
    assert Standardiser(VENDOR_SCHEMA, ["vendor_name"]).missing_columns == ["vendor_id", "account_number"]
    assert Standardiser(PAYMENT_SCHEMA, ["amount"]).missing_columns == ["transaction_id", "vendor_id", "invoice_number", "payment_date"]


def test_payment_schema_values():
    standardiser = Standardiser(PAYMENT_SCHEMA, ["Txn ID", "Vendor Code", "Invoice No", "Amount Paid", "Date Paid", "Approved By", "Cost Center", "Narration"])
    values, errors = standardiser.row(["TXN-1", "VND-1", "INV-1", "1,000", "28/09/2026", "Ada", "CC-9", "Fuel"])

    assert errors == []
    assert values["amount"] == Decimal("1000") and values["payment_date"] == date(2026, 9, 28)
    assert values["cost_centre"] == "CC-9" and values["description"] == "Fuel"


def test_vendors_upload_saves_a_snapshot(uploader, fake_storage):
    second = list(VENDOR_ROW)
    second[0], second[1] = "VND-9901", "Phantom Solutions LLC"

    response = client_for(uploader).post(VENDORS_URL, {"file": csv_file([VENDOR_ROW, second], VENDOR_HEADERS)}, format="multipart")

    assert response.status_code == 201
    body = response.json()
    assert body["upload"]["kind"] == "vendors" and body["upload"]["row_count"] == 2
    upload = DataUpload.objects.get()
    assert upload.kind == "vendors"
    assert upload.source_public_id == f"axiomvault/{uploader.organisation_id}/vendors/{upload.id}.csv"
    assert set(Vendor.objects.values_list("vendor_id", flat=True)) == {"VND-8847", "VND-9901"}
    assert Vendor.objects.filter(organisation=uploader.organisation, upload=upload).count() == 2
    assert AuditLog.objects.get().action == AuditLog.VENDORS_UPLOADED


def test_duplicate_vendor_id_saves_nothing(uploader):
    response = client_for(uploader).post(VENDORS_URL, {"file": csv_file([VENDOR_ROW, VENDOR_ROW], VENDOR_HEADERS)}, format="multipart")

    assert response.status_code == 400
    assert response.json()["validation_errors"] == [
        {"row": 3, "field": "vendor_id", "error": "Duplicate vendor: the same vendor_id as row 2."}
    ]
    assert Vendor.objects.count() == 0 and DataUpload.objects.count() == 0


def test_payments_upload_allows_the_same_invoice_twice(uploader):
    second = list(PAYMENT_ROW)
    second[0], second[4] = "TXN-90455", "2026-05-08"
    rows = [PAYMENT_ROW, second]

    response = client_for(uploader).post(PAYMENTS_URL, {"file": xlsx_file(rows, PAYMENT_HEADERS)}, format="multipart")

    assert response.status_code == 201
    assert response.json()["upload"]["kind"] == "payments"
    assert list(Payment.objects.values_list("invoice_number", flat=True)) == ["INV-2024-8847", "INV-2024-8847"]
    assert Payment.objects.get(transaction_id="TXN-90214").amount == Decimal("42350.00")
    assert AuditLog.objects.filter(action=AuditLog.PAYMENTS_UPLOADED).count() == 1


def test_bad_payments_file_saves_nothing(uploader):
    bad = list(PAYMENT_ROW)
    bad[3], bad[4] = "-5", "someday"

    body = client_for(uploader).post(PAYMENTS_URL, {"file": csv_file([PAYMENT_ROW, bad, PAYMENT_ROW], PAYMENT_HEADERS)}, format="multipart").json()

    assert body["error"] == "Some rows are invalid. Nothing was saved."
    # Rows 3 and 4 both repeat row 2's transaction_id, so both are duplicates too.
    assert {(e["row"], e["field"]) for e in body["validation_errors"]} == {
        (3, "amount"), (3, "payment_date"), (3, "transaction_id"), (4, "transaction_id")
    }
    assert Payment.objects.count() == 0


def test_vendor_and_payment_uploads_need_an_organisation_account(db):
    staff = User.objects.create_user(username="ops2", email="ops2@example.com", password="Str0ng-Passw0rd!", is_staff=True)

    assert APIClient().post(VENDORS_URL, {"file": csv_file([VENDOR_ROW], VENDOR_HEADERS)}, format="multipart").status_code == 401
    assert client_for(staff).post(PAYMENTS_URL, {"file": csv_file([PAYMENT_ROW], PAYMENT_HEADERS)}, format="multipart").status_code == 403


def test_payroll_upload_reports_its_kind(uploader):
    body = client_for(uploader).post(UPLOAD_URL, {"file": csv_file([GOOD_ROW])}, format="multipart").json()

    assert body["upload"]["kind"] == "payroll"


# ---- frontend payroll template (fix, 2026-10-22) ----

TEMPLATE_HEADERS = [
    "Employee ID", "Full Name", "Department", "Position", "Bank Account Number", "Salary Amount", "Payment Date",
    "Attendance Days", "Employment Status", "Exit Date", "Approver ID", "Pay Period",
]


def template_row(employee="EMP001", status="Active", exit_date="", attendance="22", approver="APR-01"):
    return [employee, "Ada Obi", "Operations", "Payroll Analyst", "0123456789", "230,000", "28/10/2026", attendance, status, exit_date, approver, "2026-10"]


def test_the_frontend_template_uploads(uploader):
    rows = [template_row(), template_row("EMP002", "Exited", "30/09/2026")]

    response = client_for(uploader).post(UPLOAD_URL, {"file": xlsx_file(rows, TEMPLATE_HEADERS)}, format="multipart")

    assert response.status_code == 201, response.json()
    assert response.json()["ignored_columns"] == []
    first, second = Payroll.objects.order_by("employee_id")
    assert first.role == "Payroll Analyst" and first.net_salary == Decimal("230000") and first.salary_basic is None
    assert first.attendance_days == 22 and first.employment_status == "Active" and first.approver_id == "APR-01"
    assert second.employment_status == "Exited" and second.exit_date == date(2026, 9, 30)


def test_template_cells_may_be_blank_except_exit_date_for_leavers(uploader):
    ok = client_for(uploader).post(UPLOAD_URL, {"file": csv_file([template_row(status="", attendance="")], TEMPLATE_HEADERS)}, format="multipart")
    assert ok.status_code == 201

    for status in ("Exited", "terminated", "RESIGNED"):
        response = client_for(uploader).post(UPLOAD_URL, {"file": csv_file([template_row("EMP009", status)], TEMPLATE_HEADERS)}, format="multipart")
        body = response.json()
        assert response.status_code == 400 and body["validation_errors"] == [
            {"row": 2, "field": "exit_date", "error": "Exit Date is required when Employment Status shows the employee has left."}
        ]


def test_template_still_needs_position_and_pay_period(uploader):
    headers = [h for h in TEMPLATE_HEADERS if h not in ("Position", "Pay Period")]
    row = [cell for header, cell in zip(TEMPLATE_HEADERS, template_row()) if header in headers]

    body = client_for(uploader).post(UPLOAD_URL, {"file": csv_file([row], headers)}, format="multipart").json()

    assert body["missing_columns"] == ["role", "pay_period"]


def test_approver_id_is_required(uploader):
    blank = client_for(uploader).post(UPLOAD_URL, {"file": csv_file([template_row(approver="")], TEMPLATE_HEADERS)}, format="multipart").json()
    assert blank["validation_errors"] == [{"row": 2, "field": "approver_id", "error": "This value is required."}]

    headers = [h for h in TEMPLATE_HEADERS if h != "Approver ID"]
    row = [cell for header, cell in zip(TEMPLATE_HEADERS, template_row()) if header != "Approver ID"]
    assert client_for(uploader).post(UPLOAD_URL, {"file": csv_file([row], headers)}, format="multipart").json()["missing_columns"] == ["approver_id"]


def test_a_bad_exit_date_is_reported(uploader):
    body = client_for(uploader).post(UPLOAD_URL, {"file": csv_file([template_row(status="Exited", exit_date="soon")], TEMPLATE_HEADERS)}, format="multipart").json()

    assert body["validation_errors"][0]["field"] == "exit_date" and "Use a date like" in body["validation_errors"][0]["error"]
