import django.db.models.functions.text
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0005_move_organization_text_to_organisation'),
    ]

    operations = [
        # The default lets a reverse migration re-add the column to tables that already have rows.
        migrations.AlterField(
            model_name='user',
            name='organization',
            field=models.CharField(default='', max_length=255),
        ),
        migrations.RemoveField(
            model_name='user',
            name='organization',
        ),
        migrations.AddConstraint(
            model_name='organisation',
            constraint=models.UniqueConstraint(django.db.models.functions.text.Lower('name'), name='unique_organisation_name_ci'),
        ),
    ]
