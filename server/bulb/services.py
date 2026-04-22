from datetime import datetime, timedelta
import zoneinfo

from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.utils import timezone

from .device import DeviceClient
from .models import Bulb, BulbAccess, ControlActivity, LightSchedule, PowerReading

device = DeviceClient()

# ---------------------------------------------------------
# Timing: mark bulbs offline if they stop checking in
# ---------------------------------------------------------
DEVICE_OFFLINE_AFTER_SECONDS = 90


# ---------------------------------------------------------
# Helper: Mark stale bulbs offline based on last_seen_at
# ---------------------------------------------------------
def refresh_bulb_online_statuses(stale_after_seconds: int = DEVICE_OFFLINE_AFTER_SECONDS) -> int:
    now = timezone.now()
    cutoff = now - timedelta(seconds=stale_after_seconds)

    # Bulbs that have never checked in should not stay online.
    updated_count = Bulb.objects.filter(
        is_online=True,
        is_active=True,
        last_seen_at__isnull=True,
    ).update(is_online=False)

    # Bulbs that have not checked in recently are considered offline.
    updated_count += Bulb.objects.filter(
        is_online=True,
        is_active=True,
        last_seen_at__lt=cutoff,
    ).update(is_online=False)

    return updated_count


# ---------------------------------------------------------
# Permission: Can user view this bulb?
# ---------------------------------------------------------
def user_can_view_bulb(user, bulb: Bulb) -> bool:
    if not user.is_authenticated:
        return False

    if user.is_superuser or bulb.owner_id == user.id:
        return True

    return BulbAccess.objects.filter(
        user=user,
        bulb=bulb,
        role__in=[
            BulbAccess.ROLE_OWNER,
            BulbAccess.ROLE_ADMIN,
            BulbAccess.ROLE_CONTROLLER,
            BulbAccess.ROLE_VIEWER,
        ],
    ).exists()


# ---------------------------------------------------------
# Permission: Can user control this bulb?
# ---------------------------------------------------------
def user_can_control_bulb(user, bulb: Bulb) -> bool:
    if not user.is_authenticated:
        return False

    if user.is_superuser or bulb.owner_id == user.id:
        return True

    return BulbAccess.objects.filter(
        user=user,
        bulb=bulb,
        role__in=[
            BulbAccess.ROLE_OWNER,
            BulbAccess.ROLE_ADMIN,
            BulbAccess.ROLE_CONTROLLER,
        ],
    ).exists()


# ---------------------------------------------------------
# Permission: Can user manage this bulb?
# ---------------------------------------------------------
def user_can_manage_bulb(user, bulb: Bulb) -> bool:
    if not user.is_authenticated:
        return False

    if user.is_superuser or bulb.owner_id == user.id:
        return True

    return BulbAccess.objects.filter(
        user=user,
        bulb=bulb,
        role__in=[
            BulbAccess.ROLE_OWNER,
            BulbAccess.ROLE_ADMIN,
        ],
    ).exists()


# ---------------------------------------------------------
# Lookup: Get bulb by numeric ID if user can view it
# ---------------------------------------------------------
def get_bulb_for_user(user, bulb_id) -> Bulb:
    bulb = Bulb.objects.get(id=bulb_id)
    if not user_can_view_bulb(user, bulb):
        raise PermissionDenied("You do not have access to this bulb.")
    return bulb


# ---------------------------------------------------------
# Lookup: Get bulb by UUID if user can view it
# ---------------------------------------------------------
def get_bulb_by_uuid_for_user(user, bulb_uuid) -> Bulb:
    bulb = Bulb.objects.get(uuid=bulb_uuid)
    if not user_can_view_bulb(user, bulb):
        raise PermissionDenied("You do not have access to this bulb.")
    return bulb


# ---------------------------------------------------------
# Action: Turn bulb on or off and log the action
# ---------------------------------------------------------
def set_bulb_power(bulb: Bulb, on: bool, acted_by=None, notes: str = "") -> Bulb:
    device.set_power(on)

    bulb.is_on = bool(on)
    bulb.updated_at = timezone.now()
    bulb.save(update_fields=["is_on", "updated_at"])

    ControlActivity.objects.create(
        bulb=bulb,
        user=acted_by if getattr(acted_by, "is_authenticated", False) else None,
        action=ControlActivity.ACTION_ON if on else ControlActivity.ACTION_OFF,
        notes=notes,
    )
    return bulb


# ---------------------------------------------------------
# Action: Set brightness and log the action
# ---------------------------------------------------------
def set_bulb_brightness(bulb: Bulb, brightness: int, acted_by=None, notes: str = "") -> Bulb:
    brightness = max(0, min(100, int(brightness)))

    device.set_brightness(brightness)

    bulb.brightness = brightness
    bulb.updated_at = timezone.now()
    bulb.save(update_fields=["brightness", "updated_at"])

    ControlActivity.objects.create(
        bulb=bulb,
        user=acted_by if getattr(acted_by, "is_authenticated", False) else None,
        action=ControlActivity.ACTION_BRIGHTNESS,
        value=str(brightness),
        notes=notes,
    )
    return bulb


# ---------------------------------------------------------
# Helper: Format power nicely for the UI
# ---------------------------------------------------------
def _format_power_display(power_w: float) -> str:
    return f"{round(power_w):.0f} W"


# ---------------------------------------------------------
# Helper: Format energy nicely for the UI
# ---------------------------------------------------------
def _format_energy_display(energy_wh: float) -> str:
    if abs(energy_wh) >= 1000.0:
        return f"{energy_wh / 1000.0:.2f} kWh"
    return f"{energy_wh:.1f} Wh"


# ---------------------------------------------------------
# Helper: Integrate energy over a time window from readings
# ---------------------------------------------------------
def _calculate_energy_window_wh(bulb: Bulb, window_start, window_end=None) -> float:
    if window_end is None:
        window_end = timezone.now()

    if window_start >= window_end:
        return 0.0

    # Last reading before the window helps estimate carry-over power.
    previous_reading = (
        bulb.power_readings
        .filter(created_at__lt=window_start)
        .order_by("-created_at")
        .first()
    )

    # Readings that happened inside the requested time window.
    window_readings = list(
        bulb.power_readings
        .filter(created_at__gte=window_start, created_at__lte=window_end)
        .order_by("created_at")
    )

    # If there are no readings at all, there is nothing to integrate.
    if previous_reading is None and not window_readings:
        return 0.0

    # Start with the most recent known power at the beginning of the window,
    # if we have a reading before the window begins.
    if previous_reading is not None:
        last_time = window_start
        last_power = float(previous_reading.estimated_power_w)
        remaining_readings = window_readings
    else:
        # Without a prior reading, start at the first reading inside the window.
        first_reading = window_readings[0]
        last_time = first_reading.created_at
        last_power = float(first_reading.estimated_power_w)
        remaining_readings = window_readings[1:]

    energy_wh = 0.0

    # Treat each reading as the power level until the next reading arrives.
    for reading in remaining_readings:
        segment_end = reading.created_at
        hours = max((segment_end - last_time).total_seconds(), 0.0) / 3600.0
        energy_wh += last_power * hours
        last_time = segment_end
        last_power = float(reading.estimated_power_w)

    # Extend the final reading to the end of the requested window.
    hours = max((window_end - last_time).total_seconds(), 0.0) / 3600.0
    energy_wh += last_power * hours

    return energy_wh


# ---------------------------------------------------------
# Helper: Build summary data for one bulb
# ---------------------------------------------------------
def get_bulb_power_summary(bulb: Bulb) -> dict:
    now = timezone.now()
    latest_reading = bulb.power_readings.order_by("-created_at").first()

    # Current power is only shown live if the bulb is still considered online.
    current_power_w = 0.0
    if bulb.is_online and latest_reading is not None:
        current_power_w = float(latest_reading.estimated_power_w)

    last_hour_wh = _calculate_energy_window_wh(bulb, now - timedelta(hours=1), now)
    last_day_wh = _calculate_energy_window_wh(bulb, now - timedelta(days=1), now)
    last_week_wh = _calculate_energy_window_wh(bulb, now - timedelta(days=7), now)
    last_month_wh = _calculate_energy_window_wh(bulb, now - timedelta(days=30), now)

    return {
        "has_readings": latest_reading is not None,
        "latest_reading_at": latest_reading.created_at if latest_reading else None,
        "current_power_w": current_power_w,
        "current_power_display": _format_power_display(current_power_w),
        "last_hour_wh": last_hour_wh,
        "last_hour_display": _format_energy_display(last_hour_wh),
        "last_day_wh": last_day_wh,
        "last_day_display": _format_energy_display(last_day_wh),
        "last_week_wh": last_week_wh,
        "last_week_display": _format_energy_display(last_week_wh),
        "last_month_wh": last_month_wh,
        "last_month_display": _format_energy_display(last_month_wh),
    }


# ---------------------------------------------------------
# Helper: Build combined power summary for many bulbs
# ---------------------------------------------------------
def get_account_power_summary(bulbs) -> dict:
    total_current_power_w = 0.0
    total_last_hour_wh = 0.0
    total_last_day_wh = 0.0
    total_last_week_wh = 0.0
    total_last_month_wh = 0.0
    bulb_count = 0
    bulbs_with_readings = 0

    for bulb in bulbs:
        bulb_count += 1

        summary = getattr(bulb, "power_summary", None)
        if summary is None:
            summary = get_bulb_power_summary(bulb)

        if summary["has_readings"]:
            bulbs_with_readings += 1

        total_current_power_w += float(summary["current_power_w"])
        total_last_hour_wh += float(summary["last_hour_wh"])
        total_last_day_wh += float(summary["last_day_wh"])
        total_last_week_wh += float(summary["last_week_wh"])
        total_last_month_wh += float(summary["last_month_wh"])

    return {
        "bulb_count": bulb_count,
        "bulbs_with_readings": bulbs_with_readings,
        "current_power_w": total_current_power_w,
        "current_power_display": _format_power_display(total_current_power_w),
        "last_hour_wh": total_last_hour_wh,
        "last_hour_display": _format_energy_display(total_last_hour_wh),
        "last_day_wh": total_last_day_wh,
        "last_day_display": _format_energy_display(total_last_day_wh),
        "last_week_wh": total_last_week_wh,
        "last_week_display": _format_energy_display(total_last_week_wh),
        "last_month_wh": total_last_month_wh,
        "last_month_display": _format_energy_display(total_last_month_wh),
    }


# ---------------------------------------------------------
# Helper: Safely resolve schedule timezone
# ---------------------------------------------------------
def _get_schedule_timezone(schedule: LightSchedule):
    tzname = schedule.timezone_name or "UTC"
    try:
        return zoneinfo.ZoneInfo(tzname)
    except Exception:
        return zoneinfo.ZoneInfo("UTC")


# ---------------------------------------------------------
# Compute the next run time for a schedule
# ---------------------------------------------------------
def compute_next_run(schedule: LightSchedule, from_dt=None):
    tz = _get_schedule_timezone(schedule)
    now_local = timezone.localtime(from_dt or timezone.now(), tz)

    if schedule.repeat:
        selected_days = schedule.selected_weekdays()
        if not selected_days or not schedule.time_of_day:
            return None

        base_date = now_local.date()

        for offset in range(0, 8):
            candidate_date = base_date + timedelta(days=offset)
            if candidate_date.weekday() not in selected_days:
                continue

            naive_candidate = datetime.combine(candidate_date, schedule.time_of_day)
            candidate = timezone.make_aware(naive_candidate, tz)

            if candidate > now_local:
                return candidate

        return None

    if schedule.scheduled_for:
        if timezone.is_naive(schedule.scheduled_for):
            return timezone.make_aware(schedule.scheduled_for, tz)
        return schedule.scheduled_for

    return None


# ---------------------------------------------------------
# Refresh and optionally save next run time
# ---------------------------------------------------------
def refresh_next_run(schedule: LightSchedule, from_dt=None, save=True):
    next_run = compute_next_run(schedule, from_dt=from_dt)

    if not schedule.repeat and next_run is not None and next_run <= timezone.now():
        next_run = None

    schedule.next_run_at = next_run
    if save:
        schedule.save(update_fields=["next_run_at"])
    return schedule.next_run_at


# ---------------------------------------------------------
# Apply a schedule to the bulb and update schedule state
# ---------------------------------------------------------
@transaction.atomic
def apply_schedule(schedule: LightSchedule) -> Bulb:
    bulb = schedule.bulb

    bulb = set_bulb_power(
        bulb,
        schedule.target_is_on,
        acted_by=schedule.created_by,
        notes=f"Applied by schedule #{schedule.id}",
    )

    if schedule.target_is_on and schedule.target_brightness is not None:
        bulb = set_bulb_brightness(
            bulb,
            schedule.target_brightness,
            acted_by=schedule.created_by,
            notes=f"Applied by schedule #{schedule.id}",
        )

    now = timezone.now()
    schedule.last_run_at = now
    schedule.claimed_at = None

    if schedule.repeat:
        schedule.next_run_at = compute_next_run(
            schedule,
            from_dt=now + timedelta(seconds=1),
        )
    else:
        schedule.next_run_at = None
        schedule.enabled = False

    fields_to_update = ["last_run_at", "claimed_at", "next_run_at"]
    if not schedule.repeat:
        fields_to_update.append("enabled")

    schedule.save(update_fields=fields_to_update)

    ControlActivity.objects.create(
        bulb=bulb,
        user=schedule.created_by,
        action=ControlActivity.ACTION_SCHEDULE_APPLIED,
        value=str(schedule.id),
        notes=schedule.name or "",
    )

    return bulb