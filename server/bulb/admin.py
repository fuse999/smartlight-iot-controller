from django.contrib import admin

from .models import Bulb, BulbAccess, ControlActivity, PowerReading, LightSchedule


@admin.register(Bulb)
class BulbAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "name",
        "uuid",
        "owner",
        "is_on",
        "brightness",
        "is_online",
        "is_active",
        "last_seen_at",
        "updated_at",
    )
    list_filter = ("is_active", "is_online", "created_at", "updated_at")
    search_fields = ("name", "uuid", "location_name", "owner__username", "firmware_version")
    readonly_fields = ("uuid", "device_token", "created_at", "updated_at", "last_seen_at")
    ordering = ("name", "id")


@admin.register(BulbAccess)
class BulbAccessAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "bulb", "role", "created_at")
    list_filter = ("role", "created_at")
    search_fields = ("user__username", "bulb__name")
    ordering = ("bulb", "user")


@admin.register(ControlActivity)
class ControlActivityAdmin(admin.ModelAdmin):
    list_display = ("id", "bulb", "user", "action", "value", "created_at")
    list_filter = ("action", "created_at", "bulb")
    search_fields = ("action", "value", "bulb__name", "user__username")
    ordering = ("-created_at",)


@admin.register(PowerReading)
class PowerReadingAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "bulb",
        "created_at",
        "current_rms",
        "estimated_voltage",
        "estimated_power_w",
        "cumulative_energy_wh",
    )
    list_filter = ("bulb", "created_at")
    search_fields = ("bulb__name",)
    ordering = ("-created_at",)


@admin.register(LightSchedule)
class LightScheduleAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "bulb",
        "name",
        "enabled",
        "repeat",
        "days_display",
        "time_of_day",
        "scheduled_for",
        "timezone_name",
        "next_run_at",
        "last_run_at",
    )
    list_filter = ("enabled", "repeat", "timezone_name", "bulb")
    search_fields = ("name", "bulb__name", "timezone_name")
    ordering = ("next_run_at", "id")