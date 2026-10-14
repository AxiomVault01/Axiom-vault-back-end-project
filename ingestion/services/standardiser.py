"""Turns a payroll file's columns and text cells into our standard, typed payroll fields."""

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

REQUIRED_FIELDS = [
    "employee_id",
    "employee_name",
    "department",
    "salary_basic",
    "net_salary",
    "account_number",
    "pay_period",
    "payment_date",
]

# Standard field -> other column names people use for it. When ML's template is final, only
# this table changes.
ALIASES = {
    "employee_id": ["staff_id", "employee_no", "employee_number", "emp_id"],
    "employee_name": ["name", "full_name", "staff_name"],
    "department": ["dept"],
    "salary_basic": ["basic_salary", "basic", "basic_pay"],
    "net_salary": ["net_pay", "net", "take_home"],
    "account_number": ["account_no", "bank_account", "acct_no", "account"],
    "pay_period": ["period", "payroll_period", "month"],
    "payment_date": ["pay_date", "date_paid"],
    "role": ["job_title", "designation", "position"],
    "allowances": ["allowance"],
    "deductions": ["deduction"],
    "bank_name": ["bank"],
    "attendance_days": ["days_present", "attendance"],
    "leave_days": ["days_on_leave"],
    "location": ["branch"],
    "supervisor_id": ["supervisor"],
    "employment_type": ["contract_type"],
    "grade_level": ["grade", "level"],
}
HEADER_TO_FIELD = {name: field for field, names in ALIASES.items() for name in [field, *names]}

TEXT_MAX_LENGTH = {
    "employee_id": 100,
    "employee_name": 255,
    "department": 255,
    "pay_period": 20,
    "role": 100,
    "bank_name": 100,
    "location": 100,
    "supervisor_id": 100,
    "employment_type": 50,
    "grade_level": 50,
}
MONEY_FIELDS = ["salary_basic", "net_salary", "allowances", "deductions"]
COUNT_FIELDS = ["attendance_days", "leave_days"]
DATE_FORMATS = ["%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d %H:%M:%S"]
MONEY_MAX = Decimal("9999999999.99")  # Fits DecimalField(max_digits=12, decimal_places=2).


class RowError(Exception):
    pass


def normalise_header(header: str) -> str:
    return re.sub(r"[\s\-]+", "_", str(header).strip().lower())


class PayrollStandardiser:
    def __init__(self, headers: list[str]):
        self.columns = {}  # field -> column index (first matching column wins)
        self.ignored_columns = []
        for index, header in enumerate(headers):
            field = HEADER_TO_FIELD.get(normalise_header(header))
            if field and field not in self.columns:
                self.columns[field] = index
            elif str(header).strip():
                self.ignored_columns.append(str(header).strip())
        self.missing_columns = [f for f in REQUIRED_FIELDS if f not in self.columns]

    def row(self, cells: list) -> tuple[dict | None, list[tuple[str, str]]]:
        """Returns (typed values, errors). Values is None for a fully blank row, which is skipped."""
        if all(not isinstance(cell, (date, datetime)) and not str(cell).strip() for cell in cells):
            return None, []
        values, errors = {}, []
        for field, index in self.columns.items():
            raw = cells[index] if index < len(cells) else ""
            try:
                values[field] = self._clean(field, raw)
            except RowError as exc:
                errors.append((field, str(exc)))
        return values, errors

    def _clean(self, field, raw):
        if isinstance(raw, (datetime, date)):
            if field == "payment_date":
                return raw.date() if isinstance(raw, datetime) else raw
            raw = raw.strftime("%Y-%m-%d")
        text = str(raw).strip()
        if not text:
            if field in REQUIRED_FIELDS:
                raise RowError("This value is required.")
            return Decimal("0") if field in ("allowances", "deductions") else None

        if field in MONEY_FIELDS:
            return self._money(text)
        if field in COUNT_FIELDS:
            if not re.fullmatch(r"\d+", text):
                raise RowError("Enter a whole number that is not negative.")
            return int(text)
        if field == "account_number":
            digits = re.sub(r"[\s\-]", "", text)
            if not re.fullmatch(r"\d{1,20}", digits):
                raise RowError("Use digits only, at most 20.")
            return digits
        if field == "payment_date":
            for fmt in DATE_FORMATS:
                try:
                    return datetime.strptime(text, fmt).date()
                except ValueError:
                    continue
            raise RowError("Use a date like 2026-09-28 or 28/09/2026.")
        if len(text) > TEXT_MAX_LENGTH[field]:
            raise RowError(f"Use at most {TEXT_MAX_LENGTH[field]} characters.")
        return text

    @staticmethod
    def _money(text):
        cleaned = re.sub(r"^(₦|NGN)", "", text.replace(",", "").replace(" ", ""), flags=re.IGNORECASE)
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
