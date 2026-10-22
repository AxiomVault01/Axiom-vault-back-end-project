"""Serializers for the Admin Control Panel (AxiomVault staff only)."""

from django.contrib.auth import get_user_model
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from .models import Organisation
from .services import ORGANISATION_TAKEN_MESSAGE

User = get_user_model()


class SuperAdminSummarySerializer(serializers.Serializer):
    id = serializers.UUIDField(help_text="The super admin's user ID.")
    full_name = serializers.CharField(help_text="Their name.")
    email = serializers.EmailField(help_text="Their login email.")


class OrganisationRowSerializer(serializers.ModelSerializer):
    """One row of the Organisations table."""

    super_admin = serializers.SerializerMethodField(help_text="The organisation's super admin, or `null` if none yet.")
    members_count = serializers.IntegerField(read_only=True, help_text="All accounts in the organisation, including the super admin and deactivated members.")
    active_members_count = serializers.IntegerField(read_only=True, help_text="Accounts that can log in (not deactivated).")

    class Meta:
        model = Organisation
        fields = [
            "id", "name", "status", "is_demo", "created_at", "suspended_at", "suspension_reason", "super_admin",
            "members_count", "active_members_count",
        ]
        read_only_fields = fields
        extra_kwargs = {
            "id": {"help_text": "The organisation's ID; use it in the detail, edit, suspend, and reactivate URLs."},
            "name": {"help_text": "The Cooperative's name (unique, ignoring letter case)."},
            "status": {"help_text": "`active`, or `suspended` (its users can't log in or use the API)."},
            "is_demo": {"help_text": "`true` for seeded demo data, which is removed before launch."},
            "created_at": {"help_text": "When it was created (ISO 8601)."},
            "suspended_at": {"help_text": "When it was suspended, or `null` while active."},
            "suspension_reason": {"help_text": "Why it was suspended (staff only), or empty."},
        }

    @extend_schema_field(SuperAdminSummarySerializer(allow_null=True))
    def get_super_admin(self, organisation):
        admin = next((u for u in organisation.users.all() if u.account_type == User.SUPER_ADMIN), None)
        return {"id": str(admin.id), "full_name": admin.full_name, "email": admin.email} if admin else None


class OrganisationDetailSerializer(OrganisationRowSerializer):
    """The organisation detail page."""

    alerts_count = serializers.IntegerField(read_only=True, help_text="All fraud alerts in the organisation.")
    open_alerts_count = serializers.IntegerField(read_only=True, help_text="Alerts that are `new`, `investigating`, or `escalated`.")
    cases_count = serializers.IntegerField(read_only=True, help_text="All cases.")
    uploads_count = serializers.IntegerField(read_only=True, help_text="Payroll, vendor, and payment files uploaded.")
    last_upload_at = serializers.DateTimeField(read_only=True, allow_null=True, help_text="When the latest file was uploaded, or `null`.")

    class Meta(OrganisationRowSerializer.Meta):
        fields = OrganisationRowSerializer.Meta.fields + ["alerts_count", "open_alerts_count", "cases_count", "uploads_count", "last_upload_at"]
        read_only_fields = fields


class OrganisationWriteSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=255, help_text="The Cooperative's name, 1 to 255 characters (spaces trimmed). Must be unique, ignoring letter case.")

    def validate_name(self, value):
        others = Organisation.objects.filter(name__iexact=value)
        if self.instance is not None:
            others = others.exclude(pk=self.instance.pk)
        if others.exists():
            raise serializers.ValidationError(ORGANISATION_TAKEN_MESSAGE)
        return value


class SuspendSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=500, help_text="Why it is suspended, 1 to 500 characters. Shown to staff only, not to the Cooperative.")


class OrganisationFilterSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=[c for c, _ in Organisation.STATUS_CHOICES], required=False)
    search = serializers.CharField(required=False)
