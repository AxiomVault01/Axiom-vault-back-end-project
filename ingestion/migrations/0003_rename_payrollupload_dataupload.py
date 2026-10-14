import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    """Generalises the payroll upload record to every file kind. Existing rows become kind "payroll"."""

    dependencies = [
        ('accounts', '0010_auditlog_payroll_uploaded'),
        ('ingestion', '0002_payrollupload_source_file'),
    ]

    operations = [
        migrations.RenameModel(old_name='PayrollUpload', new_name='DataUpload'),
        migrations.AlterModelTable(name='dataupload', table='data_uploads'),
        migrations.AddField(
            model_name='dataupload',
            name='kind',
            field=models.CharField(choices=[('payroll', 'Payroll'), ('vendors', 'Vendors'), ('payments', 'Payments')], default='payroll', max_length=20),
        ),
        migrations.AlterField(
            model_name='dataupload',
            name='organisation',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='data_uploads', to='accounts.organisation'),
        ),
    ]
