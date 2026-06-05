from ingestion.models import Payroll


class IngestionService:

    @staticmethod
    def save_payroll(rows):
        objects = []

        for row in rows:
            objects.append(
                Payroll(
                    employee_id=row["employee_id"],
                    employee_name=row["employee_name"],
                    department=row["department"],
                    salary_basic=row["salary_basic"],
                    net_salary=row["net_salary"],
                    account_number=row["account_number"],
                    pay_period=row["pay_period"],
                    payment_date=row["payment_date"],

                    role=row.get("role"),
                    allowances=row.get("allowances", 0),
                    deductions=row.get("deductions", 0),
                    bank_name=row.get("bank_name"),

                    attendance_days=row.get("attendance_days"),
                    leave_days=row.get("leave_days"),
                    location=row.get("location"),
                    supervisor_id=row.get("supervisor_id"),
                    employment_type=row.get("employment_type"),
                    grade_level=row.get("grade_level"),
                )
            )

        Payroll.objects.bulk_create(objects)