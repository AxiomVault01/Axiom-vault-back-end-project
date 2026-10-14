class ValidationService:
    """Checks that look across rows. Single-value rules live in the standardiser."""

    DUPLICATE_MESSAGE = "Duplicate payroll record: same employee, account number, and pay period as row {first}."

    @staticmethod
    def duplicates(rows: list[tuple[int, dict]]) -> list[dict]:
        """`rows` is (spreadsheet row number, typed values). Reports every repeat after the first."""
        errors, first_seen = [], {}
        for row_number, values in rows:
            key = (values.get("employee_id"), values.get("account_number"), values.get("pay_period"))
            if None in key:
                continue  # A missing part is already reported as its own error.
            if key in first_seen:
                errors.append({
                    "row": row_number,
                    "field": "employee_id",
                    "error": ValidationService.DUPLICATE_MESSAGE.format(first=first_seen[key]),
                })
            else:
                first_seen[key] = row_number
        return errors
