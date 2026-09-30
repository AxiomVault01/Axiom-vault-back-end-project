from ingestion.services.validation_service import ValidationService


def valid_row(**overrides):
    row = {
        "employee_id": "EMP001",
        "employee_name": "Ada Obi",
        "department": "Operations",
        "salary_basic": "250000.00",
        "net_salary": "230000.00",
        "account_number": "0123456789",
        "pay_period": "2026-09",
        "payment_date": "2026-09-28",
    }
    row.update(overrides)
    return row


def test_valid_rows_have_no_errors():
    assert ValidationService.validate([valid_row()]) == []


def test_missing_required_field_is_reported():
    errors = ValidationService.validate([valid_row(employee_name="")])

    assert errors == [{"row": 0, "error": "employee_name is missing"}]


def test_duplicate_employee_account_and_period_is_reported():
    errors = ValidationService.validate([valid_row(), valid_row()])

    assert errors == [{"row": 1, "error": "Duplicate payroll record"}]


def test_negative_net_salary_is_reported():
    errors = ValidationService.validate([valid_row(net_salary="-1")])

    assert errors == [{"row": 0, "error": "Net salary cannot be negative"}]
