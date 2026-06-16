from django.contrib import admin
from .models import User, OTP


@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    list_display = ("email", "full_name", "role", "is_verified", "is_staff")
    search_fields = ("email", "full_name")
    list_filter = ("role", "is_verified", "is_staff")
    ordering = ("-date_joined",)


@admin.register(OTP)
class OTPAdmin(admin.ModelAdmin):
    list_display = ("email", "code", "purpose", "is_used", "expires_at")
    search_fields = ("email", "code")
    # Change "type" to "purpose"
    list_filter = ("purpose", "is_used")  


