class ValidationService:
    """Checks that look across rows. Single-value rules live in the standardiser."""

    @staticmethod
    def duplicates(rows: list[tuple[int, dict]], key_fields: tuple[str, ...], message: str) -> list[dict]:
        """`rows` is (spreadsheet row number, typed values). Reports every repeat after the first."""
        errors, first_seen = [], {}
        for row_number, values in rows:
            key = tuple(values.get(f) for f in key_fields)
            if None in key:
                continue  # A missing part is already reported as its own error.
            if key in first_seen:
                errors.append({"row": row_number, "field": key_fields[0], "error": message.format(first=first_seen[key])})
            else:
                first_seen[key] = row_number
        return errors
