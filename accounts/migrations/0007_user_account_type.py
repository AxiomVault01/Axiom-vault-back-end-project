"""Adds the account type. Every existing account with an organisation becomes its super admin."""

from django.db import migrations, models


def make_existing_accounts_super_admins(apps, schema_editor):
    User = apps.get_model("accounts", "User")
    User.objects.filter(organisation__isnull=False).update(account_type="super_admin")


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0006_remove_user_organization_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='user',
            name='account_type',
            field=models.CharField(blank=True, choices=[('super_admin', 'Super Admin'), ('team_member', 'Team Member')], default='', max_length=20),
        ),
        # Reverse is a no-op: undoing the AddField removes the column anyway.
        migrations.RunPython(make_existing_accounts_super_admins, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name='user',
            constraint=models.UniqueConstraint(condition=models.Q(('account_type', 'super_admin')), fields=('organisation',), name='one_super_admin_per_organisation'),
        ),
    ]
