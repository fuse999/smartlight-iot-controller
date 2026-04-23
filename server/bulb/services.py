from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import zoneinfo

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .device import DeviceClient
from .models import (
    ActiveBulbOverride,
    Bulb,
    BulbAccess,
    ControlActivity,
    ConflictEvent,
    LightSchedule,
    PowerReading,
)

device = DeviceClient()
User = get_user_model()

# ---------------------------------------------------------
# Timing: mark bulbs offline if they stop checking in
# ---------------------------------------------------------
DEVICE_OFFLINE_AFTER_SECONDS = 90
MANUAL_OVERRIDE_MINUTES = 30
SCHEDULE_CONFLICT_WINDOW_SECONDS = 1


@dataclass
class ControlDecision:
    accepted: bool
    bulb: Bulb
    activity: ControlActivity
    reason: str = ""
    override_until: datetime | None = None


# ---------------------------------------------------------
# Helper: Mark stale bulbs offline based on last_seen_at
# ---------------------------------------------------------
def refresh_bulb_online_statuses(stale_after_seconds: int = DEVICE_OFFLINE_AFTER_SECONDS) -> int:
    now = timezone.now()
    cutoff = now - timedelta(seconds=stale_after_seconds)

    updated_count = Bulb.objects.filter(
        is_online=True,
        is_active=True,
        last_seen_at__isnull=True,
    ).update(is_online=False)

    updated_count += Bulb.objects.filter(
        is_online=True,
        is_active=True,
        last_seen_at__lt=cutoff,
    ).update(is_online=False)

    return updated_count


# ---------------------------------------------------------
# Role helpers
# ---------------------------------------------------------
def get_user_role_for_bulb(user, bulb: Bulb) -> str | None:
    if not getattr(user, "is_authenticated", False):
        return None

    if getattr(user, "is_superuser", False):
        return BulbAccess.ROLE_OWNER

    if bulb.owner_id == user.id:
        return BulbAccess.ROLE_OWNER

    access = BulbAccess.objects.filter(user=user, bulb=bulb).only("role").first()
    return access.role if access else None


def get_user_role_priority_for_bulb(user, bulb: Bulb) -> int:
    return BulbAccess.role_priority(get_user_role_for_bulb(user, bulb))


# ---------------------------------------------------------
# Permission: Can user view this bulb?
# ---------------------------------------------------------
def user_can_view_bulb(user, bulb: Bulb) -> bool:
    return get_user_role_for_bulb(user, bulb) in {
        BulbAccess.ROLE_OWNER,
        BulbAccess.ROLE_ADMIN,
        BulbAccess.ROLE_CONTROLLER,
        BulbAccess.ROLE_VIEWER,
    }


# ---------------------------------------------------------
# Permission: Can user control this bulb?
# ---------------------------------------------------------
def user_can_control_bulb(user, bulb: Bulb) -> bool:
    return get_user_role_for_bulb(user, bulb) in {
        BulbAccess.ROLE_OWNER,
        BulbAccess.ROLE_ADMIN,
        BulbAccess.ROLE_CONTROLLER,
    }


# ---------------------------------------------------------
# Permission: Can user manage this bulb?
# ---------------------------------------------------------
def user_can_manage_bulb(user, bulb: Bulb) -> bool:
    return get_user_role_for_bulb(user, bulb) in {
        BulbAccess.ROLE_OWNER,
        BulbAccess.ROLE_ADMIN,
    }


# ---------------------------------------------------------
# Permission: Can user create schedules on this bulb?
# ---------------------------------------------------------
def user_can_create_schedule(user, bulb: Bulb) -> bool:
    return user_can_control_bulb(user, bulb)


# ---------------------------------------------------------
# Permission: Can user manage a specific schedule?
# Owners/Admins can manage all schedules. Controllers can manage only their own.
# ---------------------------------------------------------
def user_can_manage_schedule(user, schedule: LightSchedule) -> bool:
    bulb = schedule.bulb
    if bulb is None:
        return False

    role = get_user_role_for_bulb(user, bulb)
    if role in {BulbAccess.ROLE_OWNER, BulbAccess.ROLE_ADMIN}:
        return True

    if role == BulbAccess.ROLE_CONTROLLER and schedule.created_by_id == getattr(user, "id", None):
        return True

    return False


# ---------------------------------------------------------
# Permission: Can user manage sharing for a bulb?
# ---------------------------------------------------------
def user_can_manage_access(user, bulb: Bulb) -> bool:
    return get_user_role_for_bulb(user, bulb) in {
        BulbAccess.ROLE_OWNER,
        BulbAccess.ROLE_ADMIN,
    }


# ---------------------------------------------------------
# Permission: Can user assign the requested role?
# Owner can assign admin/controller/viewer.
# Admin can assign controller/viewer only.
# ---------------------------------------------------------
def user_can_assign_role(user, bulb: Bulb, target_role: str) -> bool:
    actor_role = get_user_role_for_bulb(user, bulb)

    if actor_role == BulbAccess.ROLE_OWNER:
        return target_role in {
            BulbAccess.ROLE_ADMIN,
            BulbAccess.ROLE_CONTROLLER,
            BulbAccess.ROLE_VIEWER,
        }

    if actor_role == BulbAccess.ROLE_ADMIN:
        return target_role in {
            BulbAccess.ROLE_CONTROLLER,
            BulbAccess.ROLE_VIEWER,
        }

    return False


# ---------------------------------------------------------
# Access management actions
# ---------------------------------------------------------
def grant_bulb_access(*, bulb: Bulb, target_user, role: str, granted_by) -> BulbAccess:
    if not user_can_assign_role(granted_by, bulb, role):
        raise PermissionDenied("You do not have permission to assign that role.")

    if target_user.id == bulb.owner_id:
        raise PermissionDenied("The owner already has full access.")

    actor_role = get_user_role_for_bulb(granted_by, bulb)
    existing_access = BulbAccess.objects.filter(bulb=bulb, user=target_user).first()
    if actor_role == BulbAccess.ROLE_ADMIN and existing_access and existing_access.role == BulbAccess.ROLE_ADMIN:
        raise PermissionDenied("Admins cannot change the role of another admin.")

    access, _ = BulbAccess.objects.update_or_create(
        bulb=bulb,
        user=target_user,
        defaults={"role": role},
    )
    return access


def revoke_bulb_access(*, bulb: Bulb, target_user, revoked_by) -> None:
    if not user_can_manage_access(revoked_by, bulb):
        raise PermissionDenied("You do not have permission to revoke access.")

    actor_role = get_user_role_for_bulb(revoked_by, bulb)
    target_access = BulbAccess.objects.filter(bulb=bulb, user=target_user).first()
    if not target_access:
        return

    if actor_role == BulbAccess.ROLE_ADMIN and target_access.role == BulbAccess.ROLE_ADMIN:
        raise PermissionDenied("Admins cannot revoke other admins.")

    if target_user.id == bulb.owner_id:
        raise PermissionDenied("Ownership cannot be revoked here.")

    target_access.delete()


# ---------------------------------------------------------
# User lookup helper: accept username or email
# ---------------------------------------------------------
def find_user_by_identifier(identifier: str):
    identifier = (identifier or '').strip()
    if not identifier:
        return None

    user = User.objects.filter(username__iexact=identifier).first()
    if user:
        return user

    return User.objects.filter(email__iexact=identifier).first()


# ---------------------------------------------------------
# Lookup helpers
# ---------------------------------------------------------
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


# ---------------------------------------------------------
# State normalization helpers
# ---------------------------------------------------------
def _normalize_target_state(
    bulb: Bulb,
    *,
    requested_is_on: bool,
    requested_brightness: int | None,
    action: str,
) -> tuple[bool, int]:
    if action == ControlActivity.ACTION_BRIGHTNESS:
        brightness = max(0, min(100, int(requested_brightness or 0)))
        if brightness <= 0:
            return False, 0
        return True, brightness

    if not requested_is_on:
        return False, 0

    if requested_brightness is None:
        remembered = bulb.brightness if bulb.brightness > 0 else 100
        return True, remembered

    brightness = max(0, min(100, int(requested_brightness)))
    return True, brightness


# ---------------------------------------------------------
# Override helpers
# ---------------------------------------------------------
def clear_expired_overrides(bulb: Bulb) -> int:
    now = timezone.now()
    return ActiveBulbOverride.objects.filter(
        Q(is_active=False) | Q(active_until__lte=now),
        bulb=bulb,
    ).update(is_active=False, updated_at=now)



def get_active_override_for_bulb(bulb: Bulb) -> ActiveBulbOverride | None:
    clear_expired_overrides(bulb)
    return ActiveBulbOverride.objects.filter(
        bulb=bulb,
        is_active=True,
        active_until__gt=timezone.now(),
    ).first()


# ---------------------------------------------------------
# Logging helper
# ---------------------------------------------------------
def _create_activity(
    *,
    bulb: Bulb,
    acted_by,
    action: str,
    source_type: str,
    outcome: str,
    actor_role: str = "",
    actor_priority: int = 0,
    value: str = "",
    reason_code: str = "",
    reason: str = "",
    notes: str = "",
    resulting_is_on: bool | None = None,
    resulting_brightness: int | None = None,
    source_schedule: LightSchedule | None = None,
    overrode_existing: bool = False,
    overridden_activity: ControlActivity | None = None,
) -> ControlActivity:
    return ControlActivity.objects.create(
        bulb=bulb,
        user=acted_by if getattr(acted_by, "is_authenticated", False) else None,
        source_schedule=source_schedule,
        overridden_activity=overridden_activity,
        action=action,
        source_type=source_type,
        outcome=outcome,
        actor_role=actor_role or "",
        actor_priority=actor_priority,
        overrode_existing=overrode_existing,
        value=value,
        reason_code=reason_code,
        reason=reason,
        notes=notes,
        resulting_is_on=resulting_is_on,
        resulting_brightness=resulting_brightness,
    )


def _create_conflict_event(
    *,
    bulb: Bulb,
    conflict_type: str,
    reason_code: str,
    winning_activity: ControlActivity | None = None,
    losing_activity: ControlActivity | None = None,
    winner_summary: str = "",
    loser_summary: str = "",
    details: str = "",
) -> ConflictEvent:
    return ConflictEvent.objects.create(
        bulb=bulb,
        conflict_type=conflict_type,
        reason_code=reason_code,
        winning_activity=winning_activity,
        losing_activity=losing_activity,
        winner_summary=winner_summary,
        loser_summary=loser_summary,
        details=details,
    )


def _summarize_activity_source(*, actor_role: str = "", acted_by=None, source_schedule: LightSchedule | None = None) -> str:
    if source_schedule is not None:
        return f"schedule#{source_schedule.id}:{source_schedule.name or 'unnamed'}"
    if getattr(acted_by, 'is_authenticated', False):
        label = getattr(acted_by, 'username', '') or getattr(acted_by, 'email', '') or f"user#{acted_by.id}"
        return f"{label} ({actor_role or 'unknown'})"
    return actor_role or 'system'


def log_permission_denied_attempt(*, bulb: Bulb, action: str, source_type: str, acted_by=None, notes: str = "", reason: str = "Denied by permissions.") -> ControlActivity:
    actor_role = get_user_role_for_bulb(acted_by, bulb) or ""
    actor_priority = BulbAccess.role_priority(actor_role)
    activity = _create_activity(
        bulb=bulb,
        acted_by=acted_by,
        action=action,
        source_type=source_type,
        outcome=ControlActivity.OUTCOME_REJECTED,
        actor_role=actor_role,
        actor_priority=actor_priority,
        reason_code=ControlActivity.REASON_PERMISSION_DENIED,
        reason=reason,
        notes=notes,
        resulting_is_on=bulb.is_on,
        resulting_brightness=bulb.brightness,
    )
    _create_conflict_event(
        bulb=bulb,
        conflict_type=ConflictEvent.TYPE_PERMISSION,
        reason_code=ControlActivity.REASON_PERMISSION_DENIED,
        losing_activity=activity,
        loser_summary=_summarize_activity_source(actor_role=actor_role, acted_by=acted_by),
        details=reason,
    )
    return activity


# ---------------------------------------------------------
# Helper: choose the winning schedule when several fire together
# ---------------------------------------------------------
def choose_winning_due_schedule(schedule: LightSchedule, now=None) -> LightSchedule:
    now = now or timezone.now()
    window_end = now + timedelta(seconds=SCHEDULE_CONFLICT_WINDOW_SECONDS)

    candidates = list(
        LightSchedule.objects.filter(
            bulb=schedule.bulb,
            enabled=True,
            next_run_at__isnull=False,
            next_run_at__lte=window_end,
        )
    )

    if not candidates:
        return schedule

    def ranking(item: LightSchedule):
        return (
            item.created_by_role_priority,
            1 if item.is_one_time else 0,
            timezone.localtime(item.created_at),
            item.id,
        )

    return max(candidates, key=ranking)


# ---------------------------------------------------------
# Helper: advance schedule after accepted/blocked/skipped attempt
# ---------------------------------------------------------
def finalize_schedule_after_attempt(schedule: LightSchedule, *, attempted_at=None, consumed=True) -> None:
    attempted_at = attempted_at or timezone.now()
    schedule.last_run_at = attempted_at
    schedule.claimed_at = None

    if schedule.repeat:
        schedule.next_run_at = compute_next_run(schedule, from_dt=attempted_at + timedelta(seconds=1))
        schedule.save(update_fields=["last_run_at", "claimed_at", "next_run_at"])
        return

    if consumed:
        schedule.next_run_at = None
        schedule.enabled = False
        schedule.save(update_fields=["last_run_at", "claimed_at", "next_run_at", "enabled"])
    else:
        schedule.save(update_fields=["last_run_at", "claimed_at"])


# ---------------------------------------------------------
# Core control engine
# ---------------------------------------------------------
@transaction.atomic
def request_control_action(
    *,
    bulb: Bulb,
    action: str,
    requested_is_on: bool | None = None,
    requested_brightness: int | None = None,
    requested_by_user=None,
    requested_by_schedule: LightSchedule | None = None,
    source_type: str = ControlActivity.SOURCE_MANUAL,
    notes: str = "",
    override_minutes: int = MANUAL_OVERRIDE_MINUTES,
    current_rms: float | None = None,
    estimated_voltage: float | None = None,
    estimated_power_w: float | None = None,
    cumulative_energy_wh: float | None = None,
) -> ControlDecision:
    now = timezone.now()

    if requested_by_schedule is not None:
        actor_role = requested_by_schedule.created_by_role or get_user_role_for_bulb(requested_by_schedule.created_by, bulb) or ""
        actor_priority = requested_by_schedule.created_by_role_priority or BulbAccess.role_priority(actor_role)
        acted_by = requested_by_schedule.created_by
    else:
        acted_by = requested_by_user
        actor_role = get_user_role_for_bulb(acted_by, bulb) or ""
        actor_priority = BulbAccess.role_priority(actor_role)

    if source_type == ControlActivity.SOURCE_DEVICE_SYNC:
        sync_action = action or ControlActivity.ACTION_DEVICE_REPORTED
        resulting_is_on = bulb.is_on if requested_is_on is None else bool(requested_is_on)
        if requested_brightness is None:
            resulting_brightness = bulb.brightness
        else:
            resulting_brightness = max(0, min(100, int(requested_brightness)))

        bulb.is_on = resulting_is_on
        bulb.brightness = resulting_brightness
        bulb.is_online = True
        bulb.last_seen_at = now
        bulb.updated_at = now
        bulb.save(update_fields=["is_on", "brightness", "is_online", "last_seen_at", "updated_at"])

        activity = _create_activity(
            bulb=bulb,
            acted_by=None,
            action=sync_action,
            source_type=source_type,
            outcome=ControlActivity.OUTCOME_REPORTED,
            actor_role=actor_role,
            actor_priority=actor_priority,
            value="" if requested_brightness is None else str(resulting_brightness),
            reason_code=ControlActivity.REASON_DEVICE_SYNC_REPORTED,
            reason="Device synchronized reported state.",
            notes=notes,
            resulting_is_on=resulting_is_on,
            resulting_brightness=resulting_brightness,
        )

        if current_rms is not None or estimated_power_w is not None:
            PowerReading.objects.create(
                bulb=bulb,
                current_rms=float(current_rms or 0.0),
                estimated_voltage=float(estimated_voltage or 120.0),
                estimated_power_w=float(estimated_power_w or 0.0),
                cumulative_energy_wh=float(cumulative_energy_wh or 0.0),
            )

        return ControlDecision(True, bulb, activity, activity.reason)

    resulting_is_on, resulting_brightness = _normalize_target_state(
        bulb,
        requested_is_on=bool(requested_is_on),
        requested_brightness=requested_brightness,
        action=action,
    )

    active_override = get_active_override_for_bulb(bulb)

    if source_type == ControlActivity.SOURCE_SCHEDULE and requested_by_schedule is not None:
        winning_schedule = choose_winning_due_schedule(requested_by_schedule, now=now)
        if winning_schedule.id != requested_by_schedule.id:
            activity = _create_activity(
                bulb=bulb,
                acted_by=acted_by,
                action=ControlActivity.ACTION_SCHEDULE_APPLIED,
                source_type=ControlActivity.SOURCE_SCHEDULE,
                outcome=ControlActivity.OUTCOME_SKIPPED,
                actor_role=actor_role,
                actor_priority=actor_priority,
                value=str(requested_by_schedule.id),
                reason_code=ControlActivity.REASON_SUPERSEDED_BY_LATER_COMMAND,
                reason=f"Skipped because schedule #{winning_schedule.id} won the conflict.",
                notes=notes,
                resulting_is_on=bulb.is_on,
                resulting_brightness=bulb.brightness,
                source_schedule=requested_by_schedule,
            )
            _create_conflict_event(
                bulb=bulb,
                conflict_type=ConflictEvent.TYPE_SCHEDULE_VS_SCHEDULE,
                reason_code=ControlActivity.REASON_SUPERSEDED_BY_LATER_COMMAND,
                losing_activity=activity,
                winner_summary=_summarize_activity_source(source_schedule=winning_schedule),
                loser_summary=_summarize_activity_source(source_schedule=requested_by_schedule),
                details=f"Schedule #{requested_by_schedule.id} lost to schedule #{winning_schedule.id} for bulb #{bulb.id}.",
            )
            finalize_schedule_after_attempt(requested_by_schedule, attempted_at=now, consumed=True)
            return ControlDecision(False, bulb, activity, activity.reason)

    if active_override is not None:
        if source_type == ControlActivity.SOURCE_MANUAL and actor_priority < active_override.actor_priority:
            activity = _create_activity(
                bulb=bulb,
                acted_by=acted_by,
                action=action,
                source_type=source_type,
                outcome=ControlActivity.OUTCOME_REJECTED,
                actor_role=actor_role,
                actor_priority=actor_priority,
                value=str(requested_brightness) if requested_brightness is not None else "",
                reason_code=(ControlActivity.REASON_OVERRIDDEN_BY_OWNER_ADMIN if active_override.actor_role in {BulbAccess.ROLE_OWNER, BulbAccess.ROLE_ADMIN} else ControlActivity.REASON_ACTIVE_HIGHER_PRIORITY_CONTROL),
                reason="Rejected because a higher-priority manual override is active.",
                notes=notes,
                resulting_is_on=bulb.is_on,
                resulting_brightness=bulb.brightness,
            )
            _create_conflict_event(
                bulb=bulb,
                conflict_type=ConflictEvent.TYPE_MANUAL_VS_MANUAL,
                reason_code=activity.reason_code,
                winning_activity=active_override.source_activity,
                losing_activity=activity,
                winner_summary=_summarize_activity_source(actor_role=active_override.actor_role),
                loser_summary=_summarize_activity_source(actor_role=actor_role, acted_by=acted_by),
                details="Lower-priority manual command rejected because another manual override was already active.",
            )
            return ControlDecision(False, bulb, activity, activity.reason, active_override.active_until)

        if source_type == ControlActivity.SOURCE_SCHEDULE and actor_priority <= active_override.actor_priority:
            activity = _create_activity(
                bulb=bulb,
                acted_by=acted_by,
                action=ControlActivity.ACTION_SCHEDULE_APPLIED,
                source_type=ControlActivity.SOURCE_SCHEDULE,
                outcome=ControlActivity.OUTCOME_REJECTED,
                actor_role=actor_role,
                actor_priority=actor_priority,
                value=str(requested_by_schedule.id) if requested_by_schedule else "",
                reason_code=(ControlActivity.REASON_OVERRIDDEN_BY_OWNER_ADMIN if active_override.actor_role in {BulbAccess.ROLE_OWNER, BulbAccess.ROLE_ADMIN} else ControlActivity.REASON_ACTIVE_HIGHER_PRIORITY_CONTROL),
                reason="Rejected because a manual override is active.",
                notes=notes,
                resulting_is_on=bulb.is_on,
                resulting_brightness=bulb.brightness,
                source_schedule=requested_by_schedule,
                overridden_activity=active_override.source_activity,
            )
            _create_conflict_event(
                bulb=bulb,
                conflict_type=ConflictEvent.TYPE_MANUAL_VS_SCHEDULE,
                reason_code=activity.reason_code,
                winning_activity=active_override.source_activity,
                losing_activity=activity,
                winner_summary=_summarize_activity_source(actor_role=active_override.actor_role),
                loser_summary=_summarize_activity_source(source_schedule=requested_by_schedule, actor_role=actor_role, acted_by=acted_by),
                details="Schedule execution was rejected because a manual override was already active.",
            )
            if requested_by_schedule is not None:
                finalize_schedule_after_attempt(requested_by_schedule, attempted_at=now, consumed=True)
            return ControlDecision(False, bulb, activity, activity.reason, active_override.active_until)

    device.set_power(resulting_is_on)
    device.set_brightness(resulting_brightness)

    bulb.is_on = resulting_is_on
    bulb.brightness = resulting_brightness
    bulb.updated_at = now
    bulb.save(update_fields=["is_on", "brightness", "updated_at"])

    overrode_existing = active_override is not None and source_type == ControlActivity.SOURCE_MANUAL
    activity = _create_activity(
        bulb=bulb,
        acted_by=acted_by,
        action=action,
        source_type=source_type,
        outcome=ControlActivity.OUTCOME_ACCEPTED,
        actor_role=actor_role,
        actor_priority=actor_priority,
        value=str(requested_by_schedule.id) if requested_by_schedule else (str(resulting_brightness) if action == ControlActivity.ACTION_BRIGHTNESS else ""),
        reason_code=(ControlActivity.REASON_OVERRIDDEN_BY_OWNER_ADMIN if overrode_existing and actor_role in {BulbAccess.ROLE_OWNER, BulbAccess.ROLE_ADMIN} else ControlActivity.REASON_ACCEPTED),
        reason=("Accepted and overrode an earlier control." if overrode_existing else "Accepted."),
        notes=notes,
        resulting_is_on=resulting_is_on,
        resulting_brightness=resulting_brightness,
        source_schedule=requested_by_schedule,
        overrode_existing=overrode_existing,
        overridden_activity=active_override.source_activity if active_override else None,
    )

    if overrode_existing and active_override is not None:
        _create_conflict_event(
            bulb=bulb,
            conflict_type=ConflictEvent.TYPE_MANUAL_VS_MANUAL,
            reason_code=activity.reason_code or ControlActivity.REASON_SUPERSEDED_BY_LATER_COMMAND,
            winning_activity=activity,
            losing_activity=active_override.source_activity,
            winner_summary=_summarize_activity_source(actor_role=actor_role, acted_by=acted_by),
            loser_summary=_summarize_activity_source(actor_role=active_override.actor_role),
            details="A newer manual control superseded the prior active manual override.",
        )

    override_until = None
    if source_type == ControlActivity.SOURCE_MANUAL:
        override_until = now + timedelta(minutes=override_minutes)
        ActiveBulbOverride.objects.update_or_create(
            bulb=bulb,
            defaults={
                "created_by": acted_by if getattr(acted_by, "is_authenticated", False) else None,
                "source_activity": activity,
                "source_type": source_type,
                "actor_role": actor_role,
                "actor_priority": actor_priority,
                "target_is_on": resulting_is_on,
                "target_brightness": resulting_brightness,
                "started_at": now,
                "active_until": override_until,
                "is_active": True,
                "updated_at": now,
            },
        )
    elif requested_by_schedule is not None:
        finalize_schedule_after_attempt(requested_by_schedule, attempted_at=now, consumed=True)

    return ControlDecision(True, bulb, activity, activity.reason, override_until)


# Backward-compatible name used by existing code/tests.
def execute_control_request(**kwargs) -> ControlDecision:
    if "acted_by" in kwargs and "requested_by_user" not in kwargs:
        kwargs["requested_by_user"] = kwargs.pop("acted_by")
    if "source_schedule" in kwargs and "requested_by_schedule" not in kwargs:
        kwargs["requested_by_schedule"] = kwargs.pop("source_schedule")
    return request_control_action(**kwargs)


# ---------------------------------------------------------
# Compatibility wrappers used by older views
# ---------------------------------------------------------
def set_bulb_power(bulb: Bulb, on: bool, acted_by=None, notes: str = "") -> Bulb:
    decision = execute_control_request(
        bulb=bulb,
        action=ControlActivity.ACTION_ON if on else ControlActivity.ACTION_OFF,
        requested_is_on=bool(on),
        acted_by=acted_by,
        source_type=ControlActivity.SOURCE_MANUAL,
        notes=notes,
    )
    return decision.bulb



def set_bulb_brightness(bulb: Bulb, brightness: int, acted_by=None, notes: str = "") -> Bulb:
    decision = execute_control_request(
        bulb=bulb,
        action=ControlActivity.ACTION_BRIGHTNESS,
        requested_is_on=int(brightness) > 0,
        requested_brightness=brightness,
        acted_by=acted_by,
        source_type=ControlActivity.SOURCE_MANUAL,
        notes=notes,
    )
    return decision.bulb


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

    previous_reading = (
        bulb.power_readings.filter(created_at__lt=window_start).order_by("-created_at").first()
    )

    window_readings = list(
        bulb.power_readings.filter(created_at__gte=window_start, created_at__lte=window_end).order_by("created_at")
    )

    if previous_reading is None and not window_readings:
        return 0.0

    if previous_reading is not None:
        last_time = window_start
        last_power = float(previous_reading.estimated_power_w)
        remaining_readings = window_readings
    else:
        first_reading = window_readings[0]
        last_time = first_reading.created_at
        last_power = float(first_reading.estimated_power_w)
        remaining_readings = window_readings[1:]

    energy_wh = 0.0

    for reading in remaining_readings:
        segment_end = reading.created_at
        hours = max((segment_end - last_time).total_seconds(), 0.0) / 3600.0
        energy_wh += last_power * hours
        last_time = segment_end
        last_power = float(reading.estimated_power_w)

    hours = max((window_end - last_time).total_seconds(), 0.0) / 3600.0
    energy_wh += last_power * hours

    return energy_wh


# ---------------------------------------------------------
# Helper: Build summary data for an arbitrary time range
# ---------------------------------------------------------
def get_bulb_power_range_summary(bulb: Bulb, start_dt, end_dt=None) -> dict:
    end_dt = end_dt or timezone.now()
    if start_dt is None:
        first = bulb.power_readings.order_by("created_at").first()
        start_dt = first.created_at if first is not None else end_dt

    if start_dt > end_dt:
        start_dt, end_dt = end_dt, start_dt

    readings = list(
        bulb.power_readings.filter(created_at__gte=start_dt, created_at__lte=end_dt).order_by("created_at")
    )
    energy_wh = _calculate_energy_window_wh(bulb, start_dt, end_dt)
    avg_power_w = (
        sum(float(reading.estimated_power_w) for reading in readings) / len(readings)
        if readings else 0.0
    )
    peak_power_w = max((float(reading.estimated_power_w) for reading in readings), default=0.0)

    return {
        "start_dt": start_dt,
        "end_dt": end_dt,
        "reading_count": len(readings),
        "energy_wh": energy_wh,
        "energy_display": _format_energy_display(energy_wh),
        "average_power_w": avg_power_w,
        "average_power_display": _format_power_display(avg_power_w),
        "peak_power_w": peak_power_w,
        "peak_power_display": _format_power_display(peak_power_w),
    }


# ---------------------------------------------------------
# Helper: Build summary data for one bulb
# ---------------------------------------------------------
def get_bulb_power_summary(bulb: Bulb) -> dict:
    now = timezone.now()
    latest_reading = bulb.power_readings.order_by("-created_at").first()

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
    decision = execute_control_request(
        bulb=schedule.bulb,
        action=ControlActivity.ACTION_SCHEDULE_APPLIED,
        requested_is_on=schedule.target_is_on,
        requested_brightness=schedule.target_brightness,
        source_type=ControlActivity.SOURCE_SCHEDULE,
        source_schedule=schedule,
        notes=schedule.name or "",
    )
    return decision.bulb
