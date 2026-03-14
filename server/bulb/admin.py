from django.contrib import admin
from .models import LightState, ControlActivity, PowerReading


@admin.register(LightState)
class LightStateAdmin(admin.ModelAdmin):
    list_display = ("id", "is_on", "updated_at")
    list_editable = ("is_on",)
    readonly_fields = ("updated_at",)
    ordering = ("id",)

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ControlActivity)
class ControlActivityAdmin(admin.ModelAdmin):
    list_display = ("id", "action", "value", "created_at")
    list_filter = ("action", "created_at")
    search_fields = ("action", "value")
    ordering = ("-created_at",)

@admin.register(PowerReading)
class PowerReadingAdmin(admin.ModelAdmin):
    list_display = (
        "created_at",
        "current_rms",
        "estimated_power_w",
        "cumulative_energy_wh",
    )
    ordering = ("-created_at",)
