from django.contrib import admin
from .models import Organisation, User, OTP


@admin.register(Organisation)
class OrganisationAdmin(admin.ModelAdmin):
    list_display = ("name", "created_at")
    search_fields = ("name",)
    ordering = ("-created_at",)


@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    list_display = ("email", "full_name", "organisation", "account_type", "role", "is_verified", "is_staff")
    search_fields = ("email", "full_name", "organisation__name")
    list_filter = ("account_type", "role", "is_verified", "is_staff")
    list_select_related = ("organisation",)
    ordering = ("-date_joined",)


@admin.register(OTP)
class OTPAdmin(admin.ModelAdmin):
    list_display = ("email", "code", "purpose", "is_used", "expires_at")
    search_fields = ("email", "code")
    # Change "type" to "purpose"
    list_filter = ("purpose", "is_used")
