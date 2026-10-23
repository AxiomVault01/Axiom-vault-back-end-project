"""The investigation drill-down for an alert or a case: related people, linked alerts, connected transactions."""

from decimal import Decimal

from django.db.models import Q

from fraud.models import Alert
from ingestion.models import Payment, Payroll, Vendor

MAX_LINKED_ALERTS = 50
MAX_TRANSACTIONS = 100


def _amount(text) -> Decimal:
    return Decimal(str(text).replace(",", "")) if text else Decimal("0")


def _money(amount) -> str:
    return f"{Decimal(amount):,.2f}"


def _key(record):
    """What identifies a flagged record as a stored transaction, or None if it names none."""
    if record.get("transaction_id"):
        return ("payment", record["transaction_id"])
    if record.get("employee_id") and record.get("payment_date"):
        return ("salary", record["employee_id"], record["payment_date"])
    return None


class InvestigationService:
    @staticmethod
    def build(organisation, alerts, subject) -> dict:
        """`alerts` are the subject's alerts (one for an alert, every linked one for a case)."""
        employee_ids, vendor_ids, accounts = InvestigationService._scope(alerts)
        vendors = InvestigationService._latest(
            Vendor.objects.filter(organisation=organisation, vendor_id__in=list(vendor_ids)), "vendor_id"
        )
        linked = InvestigationService._linked_alerts(organisation, alerts, employee_ids, vendor_ids)
        return {
            "subject": subject,
            "alerts": alerts,
            "related": InvestigationService._related(organisation, alerts, employee_ids, vendor_ids, vendors),
            "linked_alerts": linked,
            "transactions": InvestigationService._transactions(
                organisation, [*alerts, *linked], employee_ids, vendors, vendor_ids, accounts
            ),
            "analysis": InvestigationService.analysis(alerts),
            "pattern_detections": [pattern for alert in alerts for pattern in alert.pattern_detections],
            "timeline": sorted(
                ({**event, "alert": alert.reference} for alert in alerts for event in alert.timeline),
                key=lambda event: event.get("date", ""),
            ),
        }

    @staticmethod
    def _scope(alerts):
        """Employee ID -> (name, department) and vendor ID -> name as the alerts hold them, plus account numbers."""
        employee_ids, vendor_ids, accounts = {}, {}, set()
        for alert in alerts:
            if alert.entity_type == "vendor":
                vendor_ids.setdefault(alert.entity_ref, alert.entity_name)
            else:
                employee_ids.setdefault(alert.entity_ref, (alert.entity_name, alert.department))
            for record in alert.flagged_records:
                if record.get("employee_id"):
                    employee_ids.setdefault(record["employee_id"], (record.get("employee_name", ""), record.get("department", "")))
                if record.get("account_number"):
                    accounts.add(record["account_number"])
        return employee_ids, vendor_ids, accounts

    @staticmethod
    def _latest(rows, key):
        latest = {}
        for row in rows.order_by("-created_at"):
            latest.setdefault(getattr(row, key), row)
        return latest

    @staticmethod
    def _related(organisation, alerts, employee_ids, vendor_ids, vendors):
        approver_ids, approver_names = [], []
        for alert in alerts:
            for record in alert.flagged_records:
                if record.get("approver_id") and record["approver_id"] not in approver_ids:
                    approver_ids.append(record["approver_id"])
                if record.get("approver") and record["approver"] not in approver_names:
                    approver_names.append(record["approver"])
        payroll = InvestigationService._latest(
            Payroll.objects.filter(organisation=organisation, employee_id__in=[*employee_ids, *approver_ids]), "employee_id"
        )

        def employee(employee_id, fallback=("", "")):
            row = payroll.get(employee_id)
            if row is None:
                return {"id": employee_id, "name": fallback[0], "role": "", "department": fallback[1]}
            return {"id": employee_id, "name": row.employee_name, "role": row.role or "", "department": row.department}

        employees = [employee(employee_id, fallback) for employee_id, fallback in employee_ids.items()]
        vendor_rows = []
        for vendor_id, name in vendor_ids.items():
            row = vendors.get(vendor_id)
            vendor_rows.append({
                "id": vendor_id,
                "name": row.vendor_name if row else name,
                "bank_name": (row.bank_name or "") if row else "",
                "registration_number": (row.registration_number or "") if row else "",
            })
        approvers = [employee(approver_id) for approver_id in approver_ids]
        approvers += [{"id": "", "name": name, "role": "", "department": ""} for name in approver_names]
        return {"employees": employees, "vendors": vendor_rows, "approvers": approvers}

    @staticmethod
    def _linked_alerts(organisation, alerts, employee_ids, vendor_ids):
        if not employee_ids and not vendor_ids:
            return []
        about_same = Q(entity_type="employee", entity_ref__in=list(employee_ids)) | Q(entity_type="vendor", entity_ref__in=list(vendor_ids))
        return list(
            Alert.objects.filter(about_same, organisation=organisation)
            .exclude(pk__in=[alert.pk for alert in alerts])
            .select_related("assigned_reviewer", "case")
            .order_by("-detected_at", "-number")[:MAX_LINKED_ALERTS]
        )

    @staticmethod
    def _transactions(organisation, alerts, employee_ids, vendors, vendor_ids, accounts):
        """Every stored payment and salary touching the people in scope; `alerts` decides which are flagged."""
        flagged_by = {}
        for alert in alerts:
            for record in alert.flagged_records:
                key = _key(record)
                if key and alert.reference not in flagged_by.setdefault(key, []):
                    flagged_by[key].append(alert.reference)
        names = {vendor_id: row.vendor_name for vendor_id, row in vendors.items()}
        payments = Payment.objects.filter(organisation=organisation, vendor_id__in=list(vendor_ids))
        salaries = Payroll.objects.filter(
            Q(employee_id__in=list(employee_ids)) | Q(account_number__in=list(accounts)), organisation=organisation
        )
        rows = [
            ("payment", p.transaction_id, f"{names.get(p.vendor_id, p.vendor_id)}, invoice {p.invoice_number}", p.amount, p.payment_date, ("payment", p.transaction_id))
            for p in payments.order_by("-payment_date", "transaction_id")[:MAX_TRANSACTIONS]
        ] + [
            ("salary", s.employee_id, f"Salary: {s.employee_name} ({s.pay_period})", s.net_salary, s.payment_date, ("salary", s.employee_id, s.payment_date.isoformat()))
            for s in salaries.order_by("-payment_date", "employee_id")[:MAX_TRANSACTIONS]
        ]
        rows.sort(key=lambda row: row[4], reverse=True)
        results = [
            {
                "kind": kind, "id": ref, "description": description, "amount": _money(amount), "date": date.isoformat(),
                "status": "flagged" if key in flagged_by else "clear", "alerts": flagged_by.get(key, []),
            }
            for kind, ref, description, amount, date, key in rows[:MAX_TRANSACTIONS]
        ]
        return {"count": payments.count() + salaries.count(), "results": results}

    @staticmethod
    def analysis(alerts):
        """Transaction analysis numbers over the given alerts, each flagged transaction counted once."""
        exposure, duplicated, seen = Decimal("0"), Decimal("0"), set()
        for alert in alerts:
            amounts = []
            for record in alert.flagged_records:
                amount = _amount(record.get("amount") or record.get("net_salary"))
                amounts.append(amount)
                key = _key(record) or (alert.reference, len(amounts))
                if key not in seen:
                    seen.add(key)
                    exposure += amount
            if alert.rule == Alert.DUPLICATE_PAYMENT and amounts:
                duplicated += sum(amounts) - max(amounts)
            elif alert.rule == Alert.DUPLICATE_BANK_ACCOUNT:
                duplicated += sum(amounts[1:], Decimal("0"))
        return {
            "total_exposure": _money(exposure),
            "duplicate_amount": _money(duplicated),
            "risk_score": max((alert.risk_score for alert in alerts), default=0),
            "transaction_count": len(seen),
            "currency": alerts[0].currency if alerts else "NGN",
            "source": "derived",
        }
