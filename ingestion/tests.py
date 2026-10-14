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
from ingestion.models import Payroll, PayrollUpload
from ingestion.services.file_parser import FileError, FileParser
from ingestion.services.standardiser import PayrollStandardiser
from ingestion.services.validation_service import ValidationService

User = get_user_model()
UPLOAD_URL = "/api/v1/ingestion/upload-payroll/"
HEADERS = [
    "employee_id", "employee_name", "department", "salary_basic", "net_salary",
    "account_number", "pay_period", "payment_date",
]
GOOD_ROW = ["EMP001", "Ada Obi", "Operations", "250,000.00", "230000", "0123456789", "2026-09", "28/09/2026"]


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
    standardiser = PayrollStandardiser(headers)
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
    headers, rows = FileParser.parse(xlsx_file([["EMP001", "Ada", "Ops", 250000.0, 230000.5, "0123456789", "2026-09", datetime(2026, 9, 28)]]))

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
    standardiser = PayrollStandardiser(
        ["Staff ID", "Full Name", "DEPT", "Basic Salary", "Net-Pay", "Account No", "Period", "Pay Date", "Remarks", "Branch"]
    )

    assert standardiser.missing_columns == []
    assert standardiser.columns["net_salary"] == 4
    assert standardiser.columns["location"] == 9
    assert standardiser.ignored_columns == ["Remarks"]


def test_missing_required_columns_are_listed():
    standardiser = PayrollStandardiser(["employee_id", "employee_name"])

    assert standardiser.missing_columns == HEADERS[2:]


def test_a_good_row_becomes_typed_values():
    _, [(values, errors)] = standardise([GOOD_ROW])

    assert errors == []
    assert values["salary_basic"] == Decimal("250000.00")
    assert values["account_number"] == "0123456789"
    assert values["payment_date"] == date(2026, 9, 28)


def test_blank_rows_are_skipped():
    _, [(values, errors)] = standardise([["", " ", "", "", "", "", "", ""]])

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

    errors = ValidationService.duplicates(rows)

    assert errors == [{"row": 3, "field": "employee_id", "error": ValidationService.DUPLICATE_MESSAGE.format(first=2)}]


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
    upload = PayrollUpload.objects.get()
    assert upload.organisation == uploader.organisation and upload.uploaded_by == uploader
    assert list(Payroll.objects.values_list("employee_id", flat=True).order_by("employee_id")) == ["EMP001", "EMP002"]
    assert set(Payroll.objects.values_list("organisation", "upload")) == {(uploader.organisation_id, upload.id)}
    assert Payroll.objects.get(employee_id="EMP001").account_number == "0123456789"
    audit = AuditLog.objects.get(action=AuditLog.PAYROLL_UPLOADED)
    assert audit.actor == uploader and audit.target_type == "payrollupload" and audit.target_id == str(upload.id)
    assert audit.details == {"file_name": "payroll.csv", "row_count": 2}


def test_xlsx_upload_with_aliases_and_extra_columns(uploader):
    headers = ["Staff ID", "Name", "Dept", "Basic", "Net Pay", "Account No", "Period", "Pay Date", "Remarks"]
    row = ["EMP9", "Bola", "Finance", 300000, 280000, "0011223344", "2026-09", datetime(2026, 9, 30), "ok"]

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
    assert PayrollUpload.objects.count() == 0 and Payroll.objects.count() == 0
    assert not AuditLog.objects.filter(action=AuditLog.PAYROLL_UPLOADED).exists()


def test_only_the_first_hundred_errors_are_returned(uploader):
    rows = [[f"E{i}", "A", "B", "x", "1", "1", "P", "2026-01-01"] for i in range(150)]

    body = client_for(uploader).post(UPLOAD_URL, {"file": csv_file(rows)}, format="multipart").json()

    assert body["error_count"] == 150 and len(body["validation_errors"]) == 100


@pytest.mark.parametrize(
    "file, error_start",
    [
        (lambda: csv_file([GOOD_ROW], headers=["employee_id", "employee_name"]), "Some required columns are missing"),
        (lambda: csv_file([]), "The file has no payroll rows"),
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

    assert body["missing_columns"] == HEADERS[2:]


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
    assert response.json() == {"error": "Only organisation accounts can upload payroll."}


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
