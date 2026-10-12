"""Gives every existing account its own Organisation, named from its old organization text."""

from django.db import migrations

NAME_MAX_LENGTH = 255


def unique_name(name, taken):
    """Returns name, or name with " (2)", " (3)", ... when that name (any letter case) is taken."""
    candidate, number = name, 1
    while candidate.lower() in taken:
        number += 1
        suffix = f" ({number})"
        candidate = name[: NAME_MAX_LENGTH - len(suffix)] + suffix
    taken.add(candidate.lower())
    return candidate


def text_to_organisation(apps, schema_editor):
    User = apps.get_model("accounts", "User")
    Organisation = apps.get_model("accounts", "Organisation")
    taken = set()
    # Oldest first, so the earliest account keeps the plain name.
    for user in User.objects.order_by("date_joined", "id"):
        name = (user.organization or "").strip()
        if not name:
            continue
        user.organisation = Organisation.objects.create(name=unique_name(name, taken))
        user.save(update_fields=["organisation"])


def organisation_to_text(apps, schema_editor):
    User = apps.get_model("accounts", "User")
    Organisation = apps.get_model("accounts", "Organisation")
    for user in User.objects.select_related("organisation"):
        user.organization = user.organisation.name if user.organisation else ""
        user.organisation = None
        user.save(update_fields=["organization", "organisation"])
    Organisation.objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0004_organisation_user_organisation'),
    ]

    operations = [
        migrations.RunPython(text_to_organisation, organisation_to_text),
    ]
