"""Serializers for the Admin Control Panel (AxiomVault staff only)."""

from django.contrib.auth import get_user_model
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from .models import AuditLog, Organisation
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


# ---- 21: users and staff ----

from .serializers import EMAIL_TAKEN_MESSAGE, clean_phone_number  # noqa: E402

ACCOUNT_TYPE_HELP = "`super_admin` (one per organisation, manages its team) or `team_member`."


class OrganisationSummarySerializer(serializers.Serializer):
    id = serializers.UUIDField(help_text="The organisation's ID.")
    name = serializers.CharField(help_text="Its name.")
    status = serializers.CharField(help_text="`active` or `suspended`.")


class AdminUserSerializer(serializers.ModelSerializer):
    """A Cooperative user as AxiomVault staff see them."""

    account_type = serializers.CharField(read_only=True, help_text=ACCOUNT_TYPE_HELP)
    organisation = serializers.SerializerMethodField(help_text="`{id, name, status}` of the user's Cooperative.")
    profile_picture = serializers.SerializerMethodField(help_text="Public URL of their picture, or `null`.")

    class Meta:
        model = User
        fields = [
            "id", "email", "full_name", "account_type", "role", "phone_number", "organisation", "is_active",
            "must_change_password", "profile_picture", "date_joined", "last_login",
        ]
        read_only_fields = fields
        extra_kwargs = {
            "id": {"help_text": "The user's ID; use it in the detail and action URLs."},
            "email": {"help_text": "Login email."},
            "full_name": {"help_text": "Name shown in the app."},
            "role": {"help_text": "Free-text job title, or empty."},
            "phone_number": {"help_text": "Phone number, or empty."},
            "is_active": {"help_text": "`false` after deactivation: the user can't log in."},
            "must_change_password": {"help_text": "`true` until they replace a temporary password."},
            "date_joined": {"help_text": "When the account was created (ISO 8601)."},
            "last_login": {"help_text": "Last successful login, or `null`."},
        }

    @extend_schema_field(OrganisationSummarySerializer)
    def get_organisation(self, user):
        org = user.organisation
        return {"id": str(org.id), "name": org.name, "status": org.status} if org else None

    @extend_schema_field(serializers.URLField(allow_null=True))
    def get_profile_picture(self, user):
        return user.profile_picture_url or None


class StaffSerializer(serializers.ModelSerializer):
    """An AxiomVault staff account, as the platform owner sees it."""

    admin_role = serializers.SerializerMethodField(help_text="Always `staff` here; owner accounts are not listed.")
    profile_picture = serializers.SerializerMethodField(help_text="Public URL of their picture, or `null`.")

    class Meta:
        model = User
        fields = ["id", "email", "full_name", "admin_role", "is_active", "must_change_password", "profile_picture", "date_joined", "last_login"]
        read_only_fields = fields
        extra_kwargs = AdminUserSerializer.Meta.extra_kwargs

    @extend_schema_field(serializers.CharField())
    def get_admin_role(self, user):
        return "owner" if user.is_superuser else "staff"

    @extend_schema_field(serializers.URLField(allow_null=True))
    def get_profile_picture(self, user):
        return user.profile_picture_url or None


class _EmailMixin:
    def validate_email(self, value):
        value = value.lower()
        others = User.objects.filter(email__iexact=value)
        if getattr(self, "instance", None) is not None:
            others = others.exclude(pk=self.instance.pk)
        if others.exists():
            raise serializers.ValidationError(EMAIL_TAKEN_MESSAGE)
        return value

    def validate_phone_number(self, value):
        return clean_phone_number(value) if value.strip() else ""


PASSWORD_HELP = (
    "Temporary password that you share with the person yourself (no email is sent). It must pass the normal "
    "password rules; they must replace it on first login."
)


class StaffCreateSerializer(_EmailMixin, serializers.Serializer):
    full_name = serializers.CharField(max_length=255, help_text="Full name, 1 to 255 characters.")
    email = serializers.EmailField(help_text="Login email; must not belong to any other account.")
    password = serializers.CharField(write_only=True, help_text=PASSWORD_HELP)


class AdminUserCreateSerializer(StaffCreateSerializer):
    organisation_id = serializers.UUIDField(help_text="The Cooperative the user joins.")
    account_type = serializers.ChoiceField(choices=["super_admin", "team_member"], help_text=ACCOUNT_TYPE_HELP)
    phone_number = serializers.CharField(max_length=30, required=False, allow_blank=True, help_text="Optional: 7 to 15 digits, optionally starting with +.")
    role = serializers.CharField(max_length=100, required=False, allow_blank=True, help_text="Optional job title, e.g. `Payroll Analyst`.")


class StaffUpdateSerializer(_EmailMixin, serializers.Serializer):
    """Every field is optional; only the fields sent change."""

    full_name = serializers.CharField(max_length=255, required=False, help_text="New full name.")
    email = serializers.EmailField(required=False, help_text="New login email; must not belong to another account.")


class AdminUserUpdateSerializer(StaffUpdateSerializer):
    phone_number = serializers.CharField(max_length=30, required=False, allow_blank=True, help_text="New phone number; `\"\"` clears it.")
    role = serializers.CharField(max_length=100, required=False, allow_blank=True, help_text="New job title; `\"\"` clears it.")


class AdminUserFilterSerializer(serializers.Serializer):
    organisation = serializers.UUIDField(required=False)
    account_type = serializers.ChoiceField(choices=["super_admin", "team_member"], required=False)
    is_active = serializers.BooleanField(required=False, allow_null=True, default=None)
    search = serializers.CharField(required=False)


class AuditActorSerializer(serializers.Serializer):
    id = serializers.UUIDField(allow_null=True, help_text="The user's ID, or `null` if the account was deleted.")
    email = serializers.CharField(help_text="Their email when it happened (kept after the account is deleted). Empty for system actions.")
    full_name = serializers.CharField(allow_null=True, help_text="Their current name, or `null` if the account was deleted.")


class AuditOrganisationSerializer(serializers.Serializer):
    id = serializers.UUIDField(allow_null=True, help_text="The organisation's ID, or `null` if it was deleted.")
    name = serializers.CharField(help_text="Its name when it happened (kept after it is deleted).")


class AuditLogEntrySerializer(serializers.ModelSerializer):
    """One audit log row as the Audit Log screen shows it."""

    action_label = serializers.CharField(source="get_action_display", help_text="Readable action, e.g. `Logged in`.")
    actor = serializers.SerializerMethodField(help_text="Who did it: `{id, email, full_name}`, or `null` for system actions (detection).")
    organisation = serializers.SerializerMethodField(help_text="`{id, name}` of the Cooperative, or `null` for platform actions (staff, admin panel).")

    class Meta:
        model = AuditLog
        fields = [
            "id", "sequence", "created_at", "action", "action_label", "actor", "organisation", "target_type", "target_id",
            "details", "ip_address", "previous_hash", "hash",
        ]
        read_only_fields = fields
        extra_kwargs = {
            "id": {"help_text": "The entry's ID."},
            "sequence": {"help_text": "Position in the log: 1, 2, 3, ... with no gaps."},
            "created_at": {"help_text": "When it happened (ISO 8601, UTC)."},
            "action": {"help_text": "Action code, e.g. `login`. `GET .../audit-log/actions/` lists them all."},
            "target_type": {"help_text": "Kind of record acted on, e.g. `user`, `case`, `alert`, `dataupload`; empty if none."},
            "target_id": {"help_text": "ID of that record; empty if none."},
            "details": {"help_text": "Small facts about the action, e.g. `{\"reference\": \"CASE-0001\"}`. Never passwords or tokens."},
            "ip_address": {"help_text": "The caller's IP address, or `null` (system actions, unknown)."},
            "previous_hash": {"help_text": "`hash` of the entry before it (empty for entry 1)."},
            "hash": {"help_text": "SHA-256 of this entry's content and `previous_hash`; what verify checks."},
        }

    @extend_schema_field(AuditActorSerializer(allow_null=True))
    def get_actor(self, row):
        if row.actor_id is None and not row.actor_email:
            return None
        return {
            "id": str(row.actor_id) if row.actor_id else None,
            "email": row.actor_email,
            "full_name": row.actor.full_name if row.actor_id else None,
        }

    @extend_schema_field(AuditOrganisationSerializer(allow_null=True))
    def get_organisation(self, row):
        if row.organisation_id is None and not row.organisation_name:
            return None
        return {"id": str(row.organisation_id) if row.organisation_id else None, "name": row.organisation_name}


class AuditLogFilterSerializer(serializers.Serializer):
    """Checks the Audit Log list and export query parameters."""

    action = serializers.ChoiceField(choices=[value for value, _ in AuditLog.ACTION_CHOICES], required=False)
    organisation = serializers.CharField(required=False)
    actor = serializers.UUIDField(required=False)
    target_type = serializers.CharField(required=False, max_length=50)
    target_id = serializers.CharField(required=False, max_length=64)
    date_from = serializers.DateField(required=False)
    date_to = serializers.DateField(required=False)
    search = serializers.CharField(required=False, max_length=254)

    def validate_organisation(self, value):
        if value == "none":
            return value
        try:
            return str(serializers.UUIDField().to_internal_value(value))
        except serializers.ValidationError:
            raise serializers.ValidationError("Use an organisation ID or `none`.")

    def validate(self, data):
        if data.get("date_from") and data.get("date_to") and data["date_from"] > data["date_to"]:
            raise serializers.ValidationError({"date_to": ["Must be on or after date_from."]})
        return data


class AuditActionSerializer(serializers.Serializer):
    value = serializers.CharField(help_text="Action code to send as `?action=`.")
    label = serializers.CharField(help_text="Readable name for the dropdown.")


class AuditProblemSerializer(serializers.Serializer):
    sequence = serializers.IntegerField(help_text="Sequence of the first entry that fails (or the first missing one).")
    id = serializers.UUIDField(allow_null=True, help_text="That entry's ID, or `null` when the entry is missing.")
    reason = serializers.ChoiceField(
        choices=["altered", "missing", "broken_link"],
        help_text="`altered`: its content no longer matches its hash. `missing`: entries were deleted (a gap, or "
        "entries missing at the end). `broken_link`: its `previous_hash` does not match the entry before it.",
    )
    detail = serializers.CharField(help_text="One sentence saying what was found.")


class AuditVerifySerializer(serializers.Serializer):
    valid = serializers.BooleanField(help_text="`true` when the whole chain is intact.")
    checked = serializers.IntegerField(help_text="How many entries passed before the first problem (all of them when valid).")
    last_sequence = serializers.IntegerField(help_text="Sequence of the last entry checked.")
    last_hash = serializers.CharField(help_text="Hash of the last entry checked. Write it down to compare later checks against.")
    problem = AuditProblemSerializer(allow_null=True, help_text="The first problem found, or `null`.")
    verified_at = serializers.DateTimeField(help_text="When this check ran.")