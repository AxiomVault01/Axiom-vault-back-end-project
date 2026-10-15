"""The first detection rules. Each rule returns findings; `create_alerts` turns new findings into alerts.

A finding is a plain dict with every Alert field except the organisation, number, and upload.
Rules only ever read the given organisation's rows.
"""

import logging
from collections import defaultdict
from decimal import Decimal

from django.db import transaction
from django.db.models import Max

from accounts.models import AuditLog, Organisation
from fraud.models import Alert, AlertEvent
from ingestion.models import DataUpload, Payment, Payroll, Vendor

logger = logging.getLogger(__name__)

LARGE_DUPLICATE_AMOUNT = Decimal("1000000")  # NGN


def _money(amount) -> str:
    return f"{Decimal(amount):,.2f}"


def duplicate_bank_accounts(organisation, upload) -> list[dict]:
    """One account number paid to two or more different employees in the same pay period."""
    pairs = set(upload.rows.values_list("pay_period", "account_number"))
    findings = []
    for pay_period, account_number in sorted(pairs):
        rows = list(
            Payroll.objects.filter(organisation=organisation, pay_period=pay_period, account_number=account_number)
            .order_by("employee_id", "created_at")
        )
        employees = {}
        for row in rows:
            employees.setdefault(row.employee_id, row)  # One row per employee, first seen.
        if len(employees) < 2:
            continue
        people = list(employees.values())
        count = len(people)
        first = people[0]
        names = ", ".join(p.employee_name for p in people)
        severity, score = ("critical", 95) if count >= 3 else ("high", 85)
        findings.append({
            "rule": Alert.DUPLICATE_BANK_ACCOUNT,
            "risk_type": "Duplicate Bank Account",
            "title": f"One bank account paid to {count} employees in {pay_period}",
            "explanation": (
                f"Account {account_number} received the {pay_period} salary of {count} different employees "
                f"({names}). A real employee is normally paid into their own account, so shared accounts can "
                "point to ghost workers or diverted salaries."
            ),
            "severity": severity,
            "risk_score": score,
            "confidence": score,
            "entity_type": "employee",
            "entity_name": first.employee_name,
            "entity_ref": first.employee_id,
            "department": first.department,
            "flagged_records": [
                {
                    "employee_id": p.employee_id,
                    "employee_name": p.employee_name,
                    "department": p.department,
                    "account_number": p.account_number,
                    "pay_period": p.pay_period,
                    "net_salary": _money(p.net_salary),
                    "payment_date": p.payment_date.isoformat(),
                }
                for p in people
            ],
            "timeline": sorted(
                (
                    {"date": p.payment_date.isoformat(), "label": "Salary paid", "detail": f"{p.employee_name}: {_money(p.net_salary)} to {account_number}"}
                    for p in people
                ),
                key=lambda event: event["date"],
            ),
            "fingerprint": f"{Alert.DUPLICATE_BANK_ACCOUNT}:{pay_period}:{account_number}",
        })
    return findings


def _vendor_names(organisation) -> dict:
    """vendor_id -> name from the organisation's latest vendors upload."""
    latest = DataUpload.objects.filter(organisation=organisation, kind=DataUpload.VENDORS).order_by("-created_at").first()
    if latest is None:
        return {}
    return dict(Vendor.objects.filter(upload=latest).values_list("vendor_id", "vendor_name"))


def duplicate_payments(organisation, upload) -> list[dict]:
    """The same invoice from the same vendor paid more than once, under different transaction ids."""
    pairs = set(upload.payments.values_list("vendor_id", "invoice_number"))
    names = _vendor_names(organisation) if pairs else {}
    findings = []
    for vendor_id, invoice_number in sorted(pairs):
        rows = list(
            Payment.objects.filter(organisation=organisation, vendor_id=vendor_id, invoice_number=invoice_number)
            .order_by("payment_date", "transaction_id")
        )
        payments = {}
        for row in rows:
            payments.setdefault(row.transaction_id, row)
        if len(payments) < 2:
            continue
        paid = list(payments.values())
        total = sum((p.amount for p in paid), Decimal("0"))
        duplicated = total - max(p.amount for p in paid)
        critical = duplicated >= LARGE_DUPLICATE_AMOUNT or len(paid) >= 3
        severity, score = ("critical", 98) if critical else ("high", 90)
        vendor_name = names.get(vendor_id, vendor_id)
        timeline = [
            {"date": p.payment_date.isoformat(), "label": "Payment", "detail": f"{p.transaction_id}: paid {_money(p.amount)}"}
            for p in paid
        ]
        timeline.append({"date": paid[-1].payment_date.isoformat(), "label": "Alert generated", "detail": "Duplicate payment detected"})
        findings.append({
            "rule": Alert.DUPLICATE_PAYMENT,
            "risk_type": "Duplicate Payment",
            "title": f"Invoice {invoice_number} paid {len(paid)} times to {vendor_name}",
            "explanation": (
                f"Invoice {invoice_number} from {vendor_name} was paid {len(paid)} times under different "
                f"transactions ({', '.join(payments)}), {_money(total)} in total. {_money(duplicated)} is more "
                "than the invoice should have cost and may need to be recovered."
            ),
            "severity": severity,
            "risk_score": score,
            "confidence": score,
            "entity_type": "vendor",
            "entity_name": vendor_name,
            "entity_ref": vendor_id,
            "department": paid[0].department or "",
            "flagged_records": [
                {
                    "transaction_id": p.transaction_id,
                    "payment_date": p.payment_date.isoformat(),
                    "amount": _money(p.amount),
                    "invoice_number": p.invoice_number,
                    "approver": p.approver or "",
                }
                for p in paid
            ],
            "timeline": timeline,
            "fingerprint": f"{Alert.DUPLICATE_PAYMENT}:{vendor_id}:{invoice_number}",
        })
    return findings


RULES = {
    DataUpload.PAYROLL: [duplicate_bank_accounts],
    DataUpload.PAYMENTS: [duplicate_payments],
}


def create_alerts(organisation, findings, upload) -> list[Alert]:
    """Saves findings that are not already alerts, numbering them per organisation without gaps."""
    with transaction.atomic():
        # Locking the organisation row makes concurrent uploads take numbers one after another.
        Organisation.objects.select_for_update().get(pk=organisation.pk)
        existing = set(Alert.objects.filter(organisation=organisation).values_list("fingerprint", flat=True))
        number = Alert.objects.filter(organisation=organisation).aggregate(top=Max("number"))["top"] or 0
        created = []
        for finding in findings:
            if finding["fingerprint"] in existing:
                continue
            existing.add(finding["fingerprint"])
            number += 1
            alert = Alert.objects.create(organisation=organisation, number=number, upload=upload, **finding)
            AlertEvent.objects.create(alert=alert, type=AlertEvent.CREATED, to_status=alert.status, detail=alert.title)
            created.append(alert)
        for alert in created:
            AuditLog.record(
                AuditLog.ALERT_CREATED,
                target=alert,
                organisation=organisation,
                details={"reference": alert.reference, "rule": alert.rule, "severity": alert.severity},
            )
    return created


def run_detection(upload) -> list[Alert]:
    """Runs every rule for the upload's kind and opens alerts for new findings."""
    findings = []
    for rule in RULES.get(upload.kind, []):
        findings.extend(rule(upload.organisation, upload))
    return create_alerts(upload.organisation, findings, upload)
