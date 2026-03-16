from datetime import datetime, timedelta
import zoneinfo

from django.utils import timezone

from .models import LightState, ControlActivity, LightSchedule
from .device import DeviceClient

device = DeviceClient()


def get_state() -> LightState:
    state, _ = LightState.objects.get_or_create(id=1, defaults={"is_on": False})
    return state


def set_light(on: bool) -> LightState:
    device.set_power(on)

    state = get_state()
    state.is_on = on
    state.updated_at = timezone.now()
    state.save(update_fields=["is_on", "updated_at"])

    ControlActivity.objects.create(action="ON" if on else "OFF")
    return state


def set_brightness(brightness: int) -> LightState:
    brightness = max(0, min(100, int(brightness)))

    device.set_brightness(brightness)

    state = get_state()
    state.brightness = brightness
    state.updated_at = timezone.now()
    state.save(update_fields=["brightness", "updated_at"])

    ControlActivity.objects.create(action="BRIGHTNESS", value=str(brightness))
    return state


def _get_schedule_timezone(schedule: LightSchedule):
    tzname = schedule.timezone_name or "UTC"
    try:
        return zoneinfo.ZoneInfo(tzname)
    except Exception:
        return zoneinfo.ZoneInfo("UTC")


def compute_next_run(schedule: LightSchedule, from_dt=None):
    """
    Compute the next datetime this weekly schedule should run,
    using the schedule's own saved timezone instead of the
    currently active request/worker timezone.
    """
    tz = _get_schedule_timezone(schedule)
    now_local = timezone.localtime(from_dt or timezone.now(), tz)

    selected_days = schedule.selected_weekdays()
    if not selected_days:
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


def refresh_next_run(schedule: LightSchedule, from_dt=None, save=True):
    schedule.next_run_at = compute_next_run(schedule, from_dt=from_dt)
    if save:
        schedule.save(update_fields=["next_run_at"])
    return schedule.next_run_at


def apply_schedule(schedule: LightSchedule) -> LightState:
    state = set_light(schedule.target_is_on)

    if schedule.target_is_on and schedule.target_brightness is not None:
        state = set_brightness(schedule.target_brightness)

    now = timezone.now()
    schedule.last_run_at = now
    schedule.claimed_at = None
    schedule.next_run_at = compute_next_run(schedule, from_dt=now + timedelta(seconds=1))
    schedule.save(update_fields=["last_run_at", "claimed_at", "next_run_at"])

    return state