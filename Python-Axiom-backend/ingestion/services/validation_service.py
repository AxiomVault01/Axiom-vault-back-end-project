class ValidationService:

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

    @staticmethod
    def validate(rows):
        errors = []
        seen = set()

        for index, row in enumerate(rows):

            # ❌ Missing fields
            for field in ValidationService.REQUIRED_FIELDS:
                if field not in row or row[field] in [None, ""]:
                    errors.append({
                        "row": index,
                        "error": f"{field} is missing"
                    })

            # ❌ Duplicate detection
            key = (
                row.get("employee_id"),
                row.get("account_number"),
                row.get("pay_period"),
            )

            if key in seen:
                errors.append({
                    "row": index,
                    "error": "Duplicate payroll record"
                })
            else:
                seen.add(key)

            # ❌ Logical validation
            try:
                if float(row.get("net_salary", 0)) < 0:
                    errors.append({
                        "row": index,
                        "error": "Net salary cannot be negative"
                    })
            except:
                errors.append({
                    "row": index,
                    "error": "Invalid salary format"
                })

        return errors