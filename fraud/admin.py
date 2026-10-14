from django.contrib import admin

from .models import Alert


@admin.register(Alert)
class AlertAdmin(admin.ModelAdmin):
    list_display = ("number", "risk_type", "entity_name", "severity", "status", "confidence", "organisation", "detected_at")
    list_filter = ("rule", "severity", "status")
    list_select_related = ("organisation",)
    search_fields = ("entity_name", "entity_ref", "title")
    ordering = ("-detected_at",)
