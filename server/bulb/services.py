from datetime import datetime, timedelta
import zoneinfo

from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.utils import timezone

from .device import DeviceClient
from .models import Bulb, BulbAccess, ControlActivity, LightSchedule

device = DeviceClient()


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


def get_bulb_for_user(user, bulb_id) -> Bulb:
    bulb = Bulb.objects.get(id=bulb_id)
    if not user_can_view_bulb(user, bulb):
        raise PermissionDenied("You do not have access to this bulb.")
    return bulb


def get_bulb_by_uuid_for_user(user, bulb_uuid) -> Bulb:
    bulb = Bulb.objects.get(uuid=bulb_uuid)
    if not user_can_view_bulb(user, bulb):
        raise PermissionDenied("You do not have access to this bulb.")
    return bulb


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


def _get_schedule_timezone(schedule: LightSchedule):
    tzname = schedule.timezone_name or "UTC"
    try:
        return zoneinfo.ZoneInfo(tzname)
    except Exception:
        return zoneinfo.ZoneInfo("UTC")


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


def refresh_next_run(schedule: LightSchedule, from_dt=None, save=True):
    next_run = compute_next_run(schedule, from_dt=from_dt)

    if not schedule.repeat and next_run is not None and next_run <= timezone.now():
        next_run = None

    schedule.next_run_at = next_run
    if save:
        schedule.save(update_fields=["next_run_at"])
    return schedule.next_run_at


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