import django.utils.timezone
from django.db import migrations, models

from accounts.models import audit_hash


def chain_existing_rows(apps, schema_editor):
    """Numbers and hashes the rows written before the chain existed, oldest first."""
    AuditLog = apps.get_model("accounts", "AuditLog")
    AuditChain = apps.get_model("accounts", "AuditChain")
    previous, sequence = "", 0
    for row in AuditLog.objects.order_by("created_at", "id").iterator():
        sequence += 1
        row.sequence, row.previous_hash = sequence, previous
        row.hash = audit_hash(row)
        AuditLog.objects.filter(pk=row.pk).update(sequence=row.sequence, previous_hash=row.previous_hash, hash=row.hash)
        previous = row.hash
    AuditChain.objects.update_or_create(pk=1, defaults={"last_sequence": sequence, "last_hash": previous})


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0023_case_report_exported_action"),
    ]

    operations = [
        migrations.CreateModel(
            name="AuditChain",
            fields=[
                ("id", models.PositiveSmallIntegerField(default=1, editable=False, primary_key=True, serialize=False)),
                ("last_sequence", models.PositiveBigIntegerField(default=0)),
                ("last_hash", models.CharField(blank=True, default="", max_length=64)),
            ],
            options={"db_table": "audit_chain"},
        ),
        migrations.AlterField(
            model_name="auditlog",
            name="created_at",
            field=models.DateTimeField(default=django.utils.timezone.now, editable=False),
        ),
        migrations.AddField(
            model_name="auditlog",
            name="sequence",
            field=models.PositiveBigIntegerField(editable=False, null=True),
        ),
        migrations.AddField(
            model_name="auditlog",
            name="previous_hash",
            field=models.CharField(blank=True, default="", editable=False, max_length=64),
        ),
        migrations.AddField(
            model_name="auditlog",
            name="hash",
            field=models.CharField(default="", editable=False, max_length=64),
            preserve_default=False,
        ),
        migrations.RunPython(chain_existing_rows, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="auditlog",
            name="sequence",
            field=models.PositiveBigIntegerField(editable=False, help_text="1, 2, 3, ... in the order rows were added.", unique=True),
        ),
    ]
