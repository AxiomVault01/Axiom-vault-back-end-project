"""Turns an uploaded file's columns and text cells into our standard, typed fields.

Each file kind (payroll, vendors, payments) is an `UploadSchema`: its fields, what kind of
value each holds, which are required, and the other column names people use. When ML's
templates are final, only these schemas change.
"""

import re
from dataclasses import dataclass, field as dc_field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

DATE_FORMATS = ["%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d %H:%M:%S"]
MONEY_MAX = Decimal("9999999999.99")  # Fits DecimalField(max_digits=12, decimal_places=2).
EMAIL_PATTERN = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")


@dataclass(frozen=True)
class Field:
    kind: str  # text, money, count, account, date, email, phone
    required: bool = False
    max_length: int | None = None  # text only
    aliases: tuple[str, ...] = ()
    blank: object = None  # value for an empty optional cell


@dataclass(frozen=True)
class UploadSchema:
    kind: str
    fields: dict
    duplicate_key: tuple[str, ...]
    duplicate_message: str
    # Optional check across one row's typed values; returns [(field, error)].
    row_check: object = None
    header_to_field: dict = dc_field(init=False)

    def __post_init__(self):
        lookup = {name: name_field for name_field, spec in self.fields.items() for name in [name_field, *spec.aliases]}
        object.__setattr__(self, "header_to_field", lookup)

    @property
    def required_fields(self):
        return [name for name, spec in self.fields.items() if spec.required]


def text(max_length, required=False, aliases=()):
    return Field("text", required=required, max_length=max_length, aliases=tuple(aliases))


def money(required=False, aliases=(), blank=None):
    return Field("money", required=required, aliases=tuple(aliases), blank=blank)


# Employment Status values that mean the person no longer works there (letter case ignored).
EXITED_STATUSES = {"exited", "exit", "terminated", "resigned", "retired", "dismissed", "left", "inactive"}
EXIT_DATE_REQUIRED = "Exit Date is required when Employment Status shows the employee has left."


def payroll_row_check(values: dict) -> list[tuple[str, str]]:
    status = (values.get("employment_status") or "").strip().lower()
    if status in EXITED_STATUSES and not values.get("exit_date"):
        return [("exit_date", EXIT_DATE_REQUIRED)]
    return []


# Follows the frontend's "Required Columns (payroll)" template (2026-10-22): Employee ID, Full Name,
# Department, Position, Bank Account Number, Salary Amount (net salary), Payment Date, Attendance Days,
# Employment Status, Exit Date, Approver ID (required). The pay period is not a column: the uploader sets it
# as a date range with the upload (client decision 2026-10-22).
PAYROLL_SCHEMA = UploadSchema(
    kind="payroll",
    fields={
        "employee_id": text(100, True, ["staff_id", "employee_no", "employee_number", "emp_id"]),
        "employee_name": text(255, True, ["name", "full_name", "staff_name"]),
        "department": text(255, True, ["dept"]),
        "role": text(100, True, ["position", "job_title", "designation"]),
        "account_number": Field(
            "account", required=True,
            aliases=("bank_account_number", "account_no", "bank_account", "acct_no", "account", "account_number"),
        ),
        "net_salary": money(True, ["salary_amount", "salary", "net_pay", "net", "take_home"]),
        "payment_date": Field("date", required=True, aliases=("pay_date", "date_paid")),
        "salary_basic": money(aliases=["basic_salary", "basic", "basic_pay"]),
        "allowances": money(aliases=["allowance"], blank=Decimal("0")),
        "deductions": money(aliases=["deduction"], blank=Decimal("0")),
        "bank_name": text(100, aliases=["bank"]),
        "attendance_days": Field("count", aliases=("days_present", "attendance")),
        "leave_days": Field("count", aliases=("days_on_leave",)),
        "location": text(100, aliases=["branch"]),
        "supervisor_id": text(100, aliases=["supervisor"]),
        "employment_type": text(50, aliases=["contract_type"]),
        "grade_level": text(50, aliases=["grade", "level"]),
        # Blank cells become "" (the columns store empty text, not null).
        "employment_status": Field("text", max_length=50, aliases=("status", "employee_status"), blank=""),
        "exit_date": Field("date", aliases=("date_of_exit", "exit", "date_left")),
        # Required on every row (decided 2026-10-22): who approved the payment.
        "approver_id": text(100, True, ["approver", "approved_by"]),
    },
    row_check=payroll_row_check,
    duplicate_key=("employee_id", "account_number"),
    duplicate_message="Duplicate payroll record: same employee and account number as row {first}.",
)

VENDOR_SCHEMA = UploadSchema(
    kind="vendors",
    fields={
        "vendor_id": text(100, True, ["supplier_id", "vendor_code", "vendor_no"]),
        "vendor_name": text(255, True, ["supplier_name", "vendor", "name"]),
        "account_number": Field("account", required=True, aliases=("account_no", "bank_account", "acct_no")),
        "bank_name": text(100, aliases=["bank"]),
        "registration_number": text(100, aliases=["rc_number", "cac_number", "reg_no"]),
        "address": text(255, aliases=["vendor_address"]),
        "phone": Field("phone", aliases=("phone_number", "telephone")),
        "email": Field("email", aliases=("email_address",)),
        "date_registered": Field("date", aliases=("registration_date", "onboarded")),
    },
    duplicate_key=("vendor_id",),
    duplicate_message="Duplicate vendor: the same vendor_id as row {first}.",
)

PAYMENT_SCHEMA = UploadSchema(
    kind="payments",
    fields={
        "transaction_id": text(100, True, ["txn_id", "reference", "payment_ref"]),
        "vendor_id": text(100, True, ["supplier_id", "vendor_code"]),
        "invoice_number": text(100, True, ["invoice_no", "invoice", "inv_no"]),
        "amount": money(True, ["amount_paid", "payment_amount"]),
        "payment_date": Field("date", required=True, aliases=("date_paid", "pay_date")),
        "approver": text(255, aliases=["approved_by"]),
        "cost_centre": text(100, aliases=["cost_center"]),
        "department": text(255, aliases=["dept"]),
        "description": text(500, aliases=["narration", "details"]),
    },
    duplicate_key=("transaction_id",),
    duplicate_message="Duplicate payment: the same transaction_id as row {first}.",
)


class RowError(Exception):
    pass


def normalise_header(header: str) -> str:
    return re.sub(r"[\s\-]+", "_", str(header).strip().lower())


class Standardiser:
    def __init__(self, schema: UploadSchema, headers: list[str]):
        self.schema = schema
        self.columns = {}  # field -> column index (first matching column wins)
        self.ignored_columns = []
        for index, header in enumerate(headers):
            name = schema.header_to_field.get(normalise_header(header))
            if name and name not in self.columns:
                self.columns[name] = index
            elif str(header).strip():
                self.ignored_columns.append(str(header).strip())
        self.missing_columns = [f for f in schema.required_fields if f not in self.columns]

    def row(self, cells: list) -> tuple[dict | None, list[tuple[str, str]]]:
        """Returns (typed values, errors). Values is None for a fully blank row, which is skipped."""
        if all(not isinstance(cell, (date, datetime)) and not str(cell).strip() for cell in cells):
            return None, []
        values, errors = {}, []
        for name, index in self.columns.items():
            raw = cells[index] if index < len(cells) else ""
            try:
                values[name] = self._clean(self.schema.fields[name], raw)
            except RowError as exc:
                errors.append((name, str(exc)))
        if self.schema.row_check and not errors:
            errors.extend(self.schema.row_check(values))
        return values, errors

    def _clean(self, spec: Field, raw):
        if isinstance(raw, (datetime, date)):
            if spec.kind == "date":
                return raw.date() if isinstance(raw, datetime) else raw
            raw = raw.strftime("%Y-%m-%d")
        value = str(raw).strip()
        if not value:
            if spec.required:
                raise RowError("This value is required.")
            return spec.blank if spec.blank is not None else ("" if spec.kind in ("email", "phone") else None)

        if spec.kind == "money":
            return self._money(value)
        if spec.kind == "count":
            if not re.fullmatch(r"\d+", value):
                raise RowError("Enter a whole number that is not negative.")
            return int(value)
        if spec.kind == "account":
            digits = re.sub(r"[\s\-]", "", value)
            if not re.fullmatch(r"\d{1,20}", digits):
                raise RowError("Use digits only, at most 20.")
            return digits
        if spec.kind == "date":
            for fmt in DATE_FORMATS:
                try:
                    return datetime.strptime(value, fmt).date()
                except ValueError:
                    continue
            raise RowError("Use a date like 2026-09-28 or 28/09/2026.")
        if spec.kind == "phone":
            phone = value.replace(" ", "").replace("-", "")
            if not re.fullmatch(r"\+?[0-9]{7,15}", phone):
                raise RowError("Enter a valid phone number: 7 to 15 digits, optionally starting with +.")
            return phone
        if spec.kind == "email":
            if len(value) > 254 or not EMAIL_PATTERN.fullmatch(value):
                raise RowError("Enter a valid email address.")
            return value.lower()
        if len(value) > spec.max_length:
            raise RowError(f"Use at most {spec.max_length} characters.")
        return value

    @staticmethod
    def _money(value):
        cleaned = re.sub(r"^(₦|NGN)", "", value.replace(",", "").replace(" ", ""), flags=re.IGNORECASE)
        try:
            amount = Decimal(cleaned)
        except InvalidOperation:
            raise RowError("Enter an amount like 250000 or 250,000.00.")
        if not amount.is_finite():
            raise RowError("Enter an amount like 250000 or 250,000.00.")
        if amount < 0:
            raise RowError("The amount cannot be negative.")
        if amount != amount.quantize(Decimal("0.01")):
            raise RowError("Use at most 2 decimal places.")
        if amount > MONEY_MAX:
            raise RowError("The amount is too large.")
        return amount
