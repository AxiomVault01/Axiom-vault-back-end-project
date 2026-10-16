"""Demo data for the frontend (feature 33). Everything hangs off one organisation marked `is_demo`.

`seed()` builds it through the real detection rules; `clear()` removes every demo row and
nothing else. Run them with `manage.py seed_demo` and `manage.py clear_demo --yes`.
"""

import random
from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import models, transaction
from django.utils import timezone

from accounts.models import AuditLog, Organisation
from fraud.models import Alert, AlertEvent, AlertNote, Case, CaseComment, CaseEvent
from fraud.services.detection import run_detection
from config import file_storage
from ingestion.models import DataUpload, Payment, Payroll, StoredFile, Vendor

User = get_user_model()

DEMO_ORGANISATION = "AxiomVault Demo Cooperative"
DEFAULT_PASSWORD = "Demo-Ledger-2026!"
ADMIN_EMAIL = "demo.admin@axiomvault.demo"
TEAM = [
    # email, full name, role, state
    ("ada.obi@axiomvault.demo", "Ada Obi", "Payroll Analyst", "active"),
    ("tunde.bello@axiomvault.demo", "Tunde Bello", "Internal Auditor", "active"),
    ("chioma.eze@axiomvault.demo", "Chioma Eze", "Compliance Lead", "temporary_password"),
    ("ibrahim.musa@axiomvault.demo", "Ibrahim Musa", "Finance Officer", "deactivated"),
]
DEPARTMENTS = ["Operations", "Finance", "Procurement", "Human Resources", "Internal Audit"]
FIRST_NAMES = ["Adaeze", "Bayo", "Chinedu", "Damilola", "Emeka", "Funmi", "Gbenga", "Halima", "Ifeoma", "Jide",
               "Kemi", "Lanre", "Musa", "Nkechi", "Obinna", "Patience", "Rasheed", "Sade", "Tobi", "Uche"]
LAST_NAMES = ["Adeyemi", "Okafor", "Bello", "Nwosu", "Ibrahim", "Eze", "Balogun", "Okoro", "Lawal", "Mohammed"]
VENDORS = [
    ("VND-8847", "Acme Supplies", "0012345678", "GTBank", "RC-123456"),
    ("VND-9901", "Phantom Solutions LLC", "0098765432", "Access Bank", ""),
    ("VND-3302", "Global Tech Supplies", "0023456789", "Zenith Bank", "RC-334455"),
    ("VND-4410", "Tech Innovations Inc", "0034567890", "First Bank", "RC-998877"),
    ("VND-5521", "Lagos Office Mart", "0045678901", "UBA", "RC-110022"),
    ("VND-6632", "Prime Logistics Ltd", "0056789012", "Fidelity Bank", "RC-554433"),
    ("VND-7743", "Sunrise Catering", "0067890123", "Stanbic IBTC", "RC-776655"),
    ("VND-8854", "Delta Facility Services", "0078901234", "Wema Bank", "RC-221100"),
]


class DemoExists(Exception):
    pass


def demo_organisations():
    return Organisation.objects.filter(is_demo=True)


def _upload(organisation, user, kind, name, rows):
    return DataUpload.objects.create(
        organisation=organisation, uploaded_by=user, kind=kind, file_name=name, row_count=rows, file_size=0
    )


def _payroll(organisation, admin, rng):
    """Two pay periods; one account shared by 2 employees in September and by 3 in October."""
    people = [
        (f"EMP{n:03d}", f"{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)}", DEPARTMENTS[n % len(DEPARTMENTS)])
        for n in range(1, 41)
    ]
    shared = {"2026-09": ("0555666777", ["EMP007", "EMP031"]), "2026-10": ("0888999000", ["EMP012", "EMP024", "EMP038"])}
    uploads = []
    for period, pay_day in (("2026-09", date(2026, 9, 28)), ("2026-10", date(2026, 10, 28))):
        upload = _upload(organisation, admin, DataUpload.PAYROLL, f"payroll-{period}.xlsx", len(people))
        account, sharing = shared[period]
        rows = []
        for employee_id, name, department in people:
            basic = Decimal(rng.randrange(150, 900) * 1000)
            rows.append(Payroll(
                organisation=organisation, upload=upload, employee_id=employee_id, employee_name=name,
                department=department, salary_basic=basic, net_salary=basic * Decimal("0.88"),
                account_number=account if employee_id in sharing else f"01{int(employee_id[3:]):08d}",
                pay_period=period, payment_date=pay_day, bank_name="GTBank",
            ))
        Payroll.objects.bulk_create(rows)
        uploads.append(upload)
    return uploads


def _vendors(organisation, admin):
    upload = _upload(organisation, admin, DataUpload.VENDORS, "vendors.csv", len(VENDORS))
    Vendor.objects.bulk_create(
        Vendor(organisation=organisation, upload=upload, vendor_id=vid, vendor_name=name, account_number=account,
               bank_name=bank, registration_number=rc or None, date_registered=date(2024, 1, 15))
        for vid, name, account, bank, rc in VENDORS
    )


def _payments(organisation, admin, rng):
    """About 25 payments; three invoices paid twice, one of them for more than 1,000,000 NGN."""
    rows, n = [], 0
    for day in range(1, 23):
        vid = VENDORS[day % len(VENDORS)][0]
        n += 1
        rows.append((f"TXN-{90000 + n}", vid, f"INV-2026-{1000 + n}", Decimal(rng.randrange(20, 400) * 1000), date(2026, 10, day)))
    # Three invoices paid twice: same vendor, same invoice, a new transaction id.
    first_dup, second_dup = rows[2], rows[9]
    rows += [
        ("TXN-99001", first_dup[1], first_dup[2], first_dup[3], date(2026, 10, 25)),
        ("TXN-99002", "VND-9901", "INV-PH-0001", Decimal("1250000"), date(2026, 10, 5)),
        ("TXN-99003", "VND-9901", "INV-PH-0001", Decimal("1250000"), date(2026, 10, 9)),  # Phantom, large
        ("TXN-99004", second_dup[1], second_dup[2], second_dup[3], date(2026, 10, 26)),
    ]
    upload = _upload(organisation, admin, DataUpload.PAYMENTS, "payments-october.xlsx", len(rows))
    Payment.objects.bulk_create(
        Payment(organisation=organisation, upload=upload, transaction_id=txn, vendor_id=vid, invoice_number=inv,
                amount=amount, payment_date=day, approver=rng.choice(["John Doe", "Lisa Thompson", "Mike Adewale"]),
                cost_centre=f"CC-0{day.day % 4 + 1}", department="Procurement")
        for txn, vid, inv, amount, day in rows
    )
    return upload


def _vary(alerts, admin, members):
    """Spreads the alerts over the month and gives them the statuses, reviewers, and notes the screens show."""
    plans = [
        # status, severity override, days ago, reviewer index, note
        ("investigating", None, 2, 0, "Check both approvals with the approver before escalating."),
        ("escalated", None, 5, 1, "Confirmed with the bank: the same account receives both salaries."),
        ("new", "medium", 9, None, None),
        ("false_positive", "low", 16, 0, "Second payment was refunded by the vendor on the same day."),
        ("new", None, 24, None, None),
    ]
    now = timezone.now()
    for alert, (status, severity, days_ago, reviewer, note) in zip(alerts, plans):
        when = now - timedelta(days=days_ago, hours=3)
        fields = {"detected_at": when}
        if severity:
            fields["severity"] = severity
        Alert.objects.filter(pk=alert.pk).update(**fields)
        AlertEvent.objects.filter(alert=alert, type=AlertEvent.CREATED).update(created_at=when)
        if reviewer is not None:
            person = members[reviewer]
            Alert.objects.filter(pk=alert.pk).update(assigned_reviewer=person)
            AlertEvent.objects.create(alert=alert, type=AlertEvent.REVIEWER_ASSIGNED, actor=admin, detail=person.full_name)
        if note:
            AlertNote.objects.create(alert=alert, author=admin, text=note)
            AlertEvent.objects.create(alert=alert, type=AlertEvent.NOTE_ADDED, actor=admin, detail=note)
        if status != "new":
            Alert.objects.filter(pk=alert.pk).update(status=status)
            AlertEvent.objects.create(alert=alert, type=AlertEvent.STATUS_CHANGED, actor=admin, from_status="new", to_status=status)


def _cases(alerts, admin, members):
    """Two cases: the investigating alert's case and the escalated alert's case, with history and comments."""
    now = timezone.now()
    ada, tunde = members[0], members[1]
    plans = [
        # alert index, number, status, investigator, reviewer, days ago, comment
        (0, 1, "investigating", ada, tunde, 1, "Asked payroll for both employees' bank mandates."),
        (1, 2, "escalated", tunde, ada, 4, "GTBank confirmed one account holder for all three salaries."),
    ]
    cases = []
    for index, number, status, investigator, reviewer, days_ago, comment in plans:
        alert = Alert.objects.get(pk=alerts[index].pk)
        opened = now - timedelta(days=days_ago, hours=2)
        case = Case.objects.create(
            organisation=alert.organisation, number=number, title=alert.title, priority=alert.severity, status=status,
            investigator=investigator, reviewer=reviewer, created_by=admin,
        )
        Alert.objects.filter(pk=alert.pk).update(case=case)
        CaseEvent.objects.create(case=case, type=CaseEvent.CREATED, actor=admin, to_status="new", detail=case.title)
        CaseEvent.objects.create(case=case, type=CaseEvent.INVESTIGATOR_ASSIGNED, actor=admin, detail=investigator.full_name)
        CaseEvent.objects.create(case=case, type=CaseEvent.REVIEWER_ASSIGNED, actor=admin, detail=reviewer.full_name)
        CaseEvent.objects.create(case=case, type=CaseEvent.STATUS_CHANGED, actor=investigator, from_status="new", to_status="investigating")
        CaseComment.objects.create(case=case, author=investigator, text=comment)
        CaseEvent.objects.create(case=case, type=CaseEvent.COMMENT_ADDED, actor=investigator, detail=comment)
        if status == "escalated":
            Case.objects.filter(pk=case.pk).update(
                escalation_reason="legal_risk", escalated_to=admin, escalated_at=opened + timedelta(days=2),
                escalation_notes="Possible ghost workers; legal should review before payroll runs again.",
            )
            CaseEvent.objects.create(
                case=case, type=CaseEvent.ESCALATED, actor=tunde, from_status="investigating", to_status="escalated",
                detail=f"Escalated to {admin.full_name}: Potential legal or regulatory violation",
            )
        Case.objects.filter(pk=case.pk).update(created_at=opened)
        for offset, event in enumerate(case.events.order_by("created_at")):
            CaseEvent.objects.filter(pk=event.pk).update(created_at=opened + timedelta(hours=offset * 6))
        cases.append(case)
    return cases


def seed(password=DEFAULT_PASSWORD, reset=False) -> dict:
    """Creates the demo organisation and its data. Raises DemoExists unless `reset` when it is already there."""
    with transaction.atomic():
        if demo_organisations().exists():
            if not reset:
                raise DemoExists(DEMO_ORGANISATION)
            clear()
        rng = random.Random(33)  # The same demo every time.
        organisation = Organisation.objects.create(name=DEMO_ORGANISATION, is_demo=True)
        admin = User.objects.create_user(
            username="demo-admin", email=ADMIN_EMAIL, password=password, full_name="Demo Admin",
            organisation=organisation, account_type="super_admin", department="internal_audit", is_verified=True,
        )
        members = []
        for index, (email, name, role, state) in enumerate(TEAM):
            members.append(User.objects.create_user(
                username=f"demo-member-{index}", email=email, password=password, full_name=name, role=role,
                phone_number=f"+23480300000{index}", organisation=organisation, account_type="team_member",
                is_verified=True, is_active=state != "deactivated", must_change_password=state == "temporary_password",
            ))
        payroll_uploads = _payroll(organisation, admin, rng)
        _vendors(organisation, admin)
        payments_upload = _payments(organisation, admin, rng)
        alerts = []
        for upload in [*payroll_uploads, payments_upload]:
            alerts += run_detection(upload)
        _vary(alerts, admin, members)
        _cases(alerts, admin, members)
    return {"organisation": organisation, "admin": admin, "members": members, "alerts": len(alerts)}


def demo_counts() -> dict:
    orgs = demo_organisations()
    return {
        "organisations": orgs.count(),
        "users": User.objects.filter(organisation__in=orgs).count(),
        "uploads": DataUpload.objects.filter(organisation__in=orgs).count(),
        "stored files": StoredFile.objects.filter(organisation__in=orgs).count(),
        "payroll rows": Payroll.objects.filter(organisation__in=orgs).count(),
        "vendors": Vendor.objects.filter(organisation__in=orgs).count(),
        "payments": Payment.objects.filter(organisation__in=orgs).count(),
        "alerts": Alert.objects.filter(organisation__in=orgs).count(),
        "cases": Case.objects.filter(organisation__in=orgs).count(),
        "audit rows": AuditLog.objects.filter(organisation__in=orgs).count(),
    }


def clear() -> dict:
    """Deletes every row of every demo organisation, in an order the PROTECT links allow. Nothing else."""
    counts = demo_counts()
    with transaction.atomic():
        orgs = list(demo_organisations())
        Case.objects.filter(organisation__in=orgs).delete()  # Their events and comments go with them.
        Alert.objects.filter(organisation__in=orgs).delete()  # Events and notes go with them (cascade).
        for model in (Payroll, Vendor, Payment):
            model.objects.filter(organisation__in=orgs).delete()
        uploads = DataUpload.objects.filter(organisation__in=orgs)
        files = StoredFile.objects.filter(organisation__in=orgs)
        stored = [*uploads.exclude(source_public_id="").values_list("source_public_id", flat=True), *files.values_list("public_id", flat=True)]
        uploads.delete()
        files.delete()  # After cases, whose evidence protects them.
        # Remove the files from storage only once the rows are really gone.
        transaction.on_commit(lambda: [file_storage.delete(public_id) for public_id in stored])
        # Audit rows refuse deletes on purpose; demo rows are the one sanctioned exception.
        models.QuerySet(AuditLog).filter(organisation__in=orgs).delete()
        users = User.objects.filter(organisation__in=orgs)
        models.QuerySet(AuditLog).filter(actor__in=users).delete()
        users.delete()
        Organisation.objects.filter(pk__in=[o.pk for o in orgs], is_demo=True).delete()
    return counts
