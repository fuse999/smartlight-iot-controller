import secrets
import string
import uuid

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone


def generate_device_token() -> str:
    return secrets.token_urlsafe(32)


def generate_claim_code() -> str:
    alphabet = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(8))


class Bulb(models.Model):
    name = models.CharField(max_length=100)
    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    description = models.TextField(blank=True, default="")
    location_name = models.CharField(max_length=100, blank=True, default="")
    pairing_mode_enabled = models.BooleanField(default=False)
    pairing_expires_at = models.DateTimeField(null=True, blank=True, db_index=True)

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="owned_bulbs",
        null=True,
        blank=True,
    )

    device_token = models.CharField(
        max_length=128,
        unique=True,
        default=generate_device_token,
        editable=False,
    )

    claim_code = models.CharField(
        max_length=16,
        default=generate_claim_code,
        editable=False,
        db_index=True,
    )

    claimed_at = models.DateTimeField(null=True, blank=True)

    is_active = models.BooleanField(default=True)
    is_online = models.BooleanField(default=False)
    firmware_version = models.CharField(max_length=50, blank=True, default="")
    last_seen_at = models.DateTimeField(null=True, blank=True)

    is_on = models.BooleanField(default=False)
    brightness = models.PositiveSmallIntegerField(
        default=100,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    updated_at = models.DateTimeField(default=timezone.now)

    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["name", "id"]

    def __str__(self) -> str:
        return self.name

    @property
    def is_claimed(self) -> bool:
        return self.owner_id is not None

    @property
    def is_claimable(self) -> bool:
        return (
            self.owner_id is None
            and self.pairing_mode_enabled
            and self.pairing_expires_at is not None
            and self.pairing_expires_at > timezone.now()
        )


class BulbAccess(models.Model):
    ROLE_OWNER = "owner"
    ROLE_ADMIN = "admin"
    ROLE_CONTROLLER = "controller"
    ROLE_VIEWER = "viewer"

    ROLE_CHOICES = [
        (ROLE_OWNER, "Owner"),
        (ROLE_ADMIN, "Admin"),
        (ROLE_CONTROLLER, "Controller"),
        (ROLE_VIEWER, "Viewer"),
    ]

    ROLE_PRIORITY = {
        ROLE_VIEWER: 100,
        ROLE_CONTROLLER: 200,
        ROLE_ADMIN: 300,
        ROLE_OWNER: 400,
    }

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="bulb_access",
    )
    bulb = models.ForeignKey(
        Bulb,
        on_delete=models.CASCADE,
        related_name="user_access",
    )
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default=ROLE_VIEWER)

    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        unique_together = [("user", "bulb")]
        ordering = ["bulb_id", "user_id"]

    def __str__(self) -> str:
        return f"{self.user} -> {self.bulb} ({self.role})"

    @classmethod
    def role_priority(cls, role: str | None) -> int:
        return cls.ROLE_PRIORITY.get(role or "", 0)


class ControlActivity(models.Model):
    ACTION_ON = "ON"
    ACTION_OFF = "OFF"
    ACTION_BRIGHTNESS = "BRIGHTNESS"
    ACTION_SCHEDULE_APPLIED = "SCHEDULE_APPLIED"
    ACTION_DEVICE_REPORTED = "DEVICE_REPORTED"

    ACTION_CHOICES = [
        (ACTION_ON, "Turn ON"),
        (ACTION_OFF, "Turn OFF"),
        (ACTION_BRIGHTNESS, "Set Brightness"),
        (ACTION_SCHEDULE_APPLIED, "Schedule Applied"),
        (ACTION_DEVICE_REPORTED, "Device Reported"),
    ]

    SOURCE_MANUAL = "manual"
    SOURCE_SCHEDULE = "schedule"
    SOURCE_DEVICE = "device"
    SOURCE_DEVICE_SYNC = "device-sync"
    SOURCE_SYSTEM = "system"

    SOURCE_CHOICES = [
        (SOURCE_MANUAL, "Manual"),
        (SOURCE_SCHEDULE, "Schedule"),
        (SOURCE_DEVICE, "Device"),
        (SOURCE_DEVICE_SYNC, "Device Sync"),
        (SOURCE_SYSTEM, "System"),
    ]

    OUTCOME_ACCEPTED = "accepted"
    OUTCOME_REJECTED = "rejected"
    OUTCOME_SKIPPED = "skipped"
    OUTCOME_REPORTED = "reported"

    OUTCOME_CHOICES = [
        (OUTCOME_ACCEPTED, "Accepted"),
        (OUTCOME_REJECTED, "Rejected"),
        (OUTCOME_SKIPPED, "Skipped"),
        (OUTCOME_REPORTED, "Reported"),
    ]

    REASON_ACCEPTED = "accepted"
    REASON_PERMISSION_DENIED = "permission_denied"
    REASON_ACTIVE_HIGHER_PRIORITY_CONTROL = "active_higher_priority_control"
    REASON_SUPERSEDED_BY_LATER_COMMAND = "superseded_by_later_command"
    REASON_OVERRIDDEN_BY_OWNER_ADMIN = "overridden_by_owner_admin"
    REASON_DEVICE_SYNC_REPORTED = "device_sync_reported"

    REASON_CODE_CHOICES = [
        (REASON_ACCEPTED, "Accepted"),
        (REASON_PERMISSION_DENIED, "Denied by permissions"),
        (REASON_ACTIVE_HIGHER_PRIORITY_CONTROL, "Denied by active higher-priority control"),
        (REASON_SUPERSEDED_BY_LATER_COMMAND, "Superseded by later command"),
        (REASON_OVERRIDDEN_BY_OWNER_ADMIN, "Overridden by owner/admin"),
        (REASON_DEVICE_SYNC_REPORTED, "Device sync reported"),
    ]

    bulb = models.ForeignKey(
        Bulb,
        on_delete=models.CASCADE,
        related_name="activities",
        null=True,
        blank=True,
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="bulb_activities",
    )
    source_schedule = models.ForeignKey(
        "LightSchedule",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="control_activities",
    )
    overridden_activity = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="override_children",
    )

    action = models.CharField(max_length=32, choices=ACTION_CHOICES)
    source_type = models.CharField(max_length=16, choices=SOURCE_CHOICES, default=SOURCE_MANUAL)
    outcome = models.CharField(max_length=16, choices=OUTCOME_CHOICES, default=OUTCOME_ACCEPTED)
    actor_role = models.CharField(max_length=20, blank=True, default="")
    actor_priority = models.IntegerField(default=0)
    overrode_existing = models.BooleanField(default=False)

    value = models.CharField(max_length=64, blank=True, default="")
    reason_code = models.CharField(max_length=64, choices=REASON_CODE_CHOICES, blank=True, default="")
    reason = models.CharField(max_length=255, blank=True, default="")
    notes = models.TextField(blank=True, default="")
    resulting_is_on = models.BooleanField(null=True, blank=True)
    resulting_brightness = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        bulb_name = self.bulb.name if self.bulb_id else "Unknown bulb"
        return f"{bulb_name}: {self.action} ({self.outcome}) @ {self.created_at:%Y-%m-%d %H:%M:%S}"


class LightSchedule(models.Model):
    bulb = models.ForeignKey(
        Bulb,
        on_delete=models.CASCADE,
        related_name="schedules",
        null=True,
        blank=True,
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_bulb_schedules",
    )

    name = models.CharField(max_length=100, blank=True, default="")

    enabled = models.BooleanField(default=True)
    created_at = models.DateTimeField(default=timezone.now)
    last_run_at = models.DateTimeField(null=True, blank=True)
    next_run_at = models.DateTimeField(null=True, blank=True, db_index=True)
    claimed_at = models.DateTimeField(null=True, blank=True, db_index=True)

    target_is_on = models.BooleanField()
    target_brightness = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(0), MaxValueValidator(100)],
        null=True,
        blank=True,
    )

    repeat = models.BooleanField(default=False)

    scheduled_for = models.DateTimeField(null=True, blank=True)
    time_of_day = models.TimeField(null=True, blank=True)

    monday = models.BooleanField(default=False)
    tuesday = models.BooleanField(default=False)
    wednesday = models.BooleanField(default=False)
    thursday = models.BooleanField(default=False)
    friday = models.BooleanField(default=False)
    saturday = models.BooleanField(default=False)
    sunday = models.BooleanField(default=False)

    timezone_name = models.CharField(max_length=64, default="UTC")
    created_by_role = models.CharField(max_length=20, choices=BulbAccess.ROLE_CHOICES, blank=True, default="")
    created_by_role_priority = models.IntegerField(default=0)

    class Meta:
        ordering = ["next_run_at", "id"]

    def selected_weekdays(self):
        days = []
        if self.monday:
            days.append(0)
        if self.tuesday:
            days.append(1)
        if self.wednesday:
            days.append(2)
        if self.thursday:
            days.append(3)
        if self.friday:
            days.append(4)
        if self.saturday:
            days.append(5)
        if self.sunday:
            days.append(6)
        return days

    def days_display(self) -> str:
        parts = []
        if self.monday:
            parts.append("Mon")
        if self.tuesday:
            parts.append("Tue")
        if self.wednesday:
            parts.append("Wed")
        if self.thursday:
            parts.append("Thu")
        if self.friday:
            parts.append("Fri")
        if self.saturday:
            parts.append("Sat")
        if self.sunday:
            parts.append("Sun")
        return ", ".join(parts) if parts else "No days selected"

    @property
    def is_one_time(self) -> bool:
        return not self.repeat

    def __str__(self) -> str:
        bri = "" if self.target_brightness is None else f", bri={self.target_brightness}"

        if self.repeat and self.time_of_day:
            schedule_part = f"[{self.days_display()} @ {self.time_of_day:%H:%M} {self.timezone_name}]"
        elif self.scheduled_for:
            schedule_part = f"[once @ {self.scheduled_for:%Y-%m-%d %H:%M} {self.timezone_name}]"
        else:
            schedule_part = "[unscheduled]"

        return (
            f"{self.bulb.name} - "
            f"{'ENABLED' if self.enabled else 'DISABLED'} "
            f"{schedule_part} -> on={self.target_is_on}{bri}"
        )




class ConflictEvent(models.Model):
    TYPE_PERMISSION = "permission"
    TYPE_MANUAL_VS_MANUAL = "manual_vs_manual"
    TYPE_MANUAL_VS_SCHEDULE = "manual_vs_schedule"
    TYPE_SCHEDULE_VS_SCHEDULE = "schedule_vs_schedule"

    TYPE_CHOICES = [
        (TYPE_PERMISSION, "Permission"),
        (TYPE_MANUAL_VS_MANUAL, "Manual vs Manual"),
        (TYPE_MANUAL_VS_SCHEDULE, "Manual vs Schedule"),
        (TYPE_SCHEDULE_VS_SCHEDULE, "Schedule vs Schedule"),
    ]

    bulb = models.ForeignKey(
        Bulb,
        on_delete=models.CASCADE,
        related_name="conflict_events",
    )
    conflict_type = models.CharField(max_length=32, choices=TYPE_CHOICES)
    reason_code = models.CharField(max_length=64, choices=ControlActivity.REASON_CODE_CHOICES, blank=True, default="")
    winning_activity = models.ForeignKey(
        ControlActivity,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="won_conflict_events",
    )
    losing_activity = models.ForeignKey(
        ControlActivity,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="lost_conflict_events",
    )
    winner_summary = models.CharField(max_length=255, blank=True, default="")
    loser_summary = models.CharField(max_length=255, blank=True, default="")
    details = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.bulb.name}: {self.conflict_type} ({self.reason_code or 'none'})"


class PowerReading(models.Model):
    bulb = models.ForeignKey(
        Bulb,
        on_delete=models.CASCADE,
        related_name="power_readings",
        null=True,
        blank=True,
    )

    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    current_rms = models.FloatField(help_text="Measured current in amps")
    estimated_voltage = models.FloatField(
        help_text="Estimated voltage of the mains",
        default=120,
    )
    estimated_power_w = models.FloatField(help_text="Calculated power of the mains")
    cumulative_energy_wh = models.FloatField(default=0.0)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.bulb.name}: {self.estimated_power_w:.2f}W @ {self.created_at:%Y-%m-%d %H:%M:%S}"


class ActiveBulbOverride(models.Model):
    bulb = models.OneToOneField(
        Bulb,
        on_delete=models.CASCADE,
        related_name="active_override",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_bulb_overrides",
    )
    source_activity = models.ForeignKey(
        ControlActivity,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="override_records",
    )
    source_type = models.CharField(
        max_length=16,
        choices=ControlActivity.SOURCE_CHOICES,
        default=ControlActivity.SOURCE_MANUAL,
    )
    actor_role = models.CharField(max_length=20, blank=True, default="")
    actor_priority = models.IntegerField(default=0)
    target_is_on = models.BooleanField(default=False)
    target_brightness = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    started_at = models.DateTimeField(default=timezone.now)
    active_until = models.DateTimeField(db_index=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-active_until"]

    @property
    def is_current(self) -> bool:
        return self.is_active and self.active_until > timezone.now()

    def __str__(self) -> str:
        return f"Override for {self.bulb.name} until {self.active_until:%Y-%m-%d %H:%M:%S}"
