import secrets
import uuid
import string

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

    action = models.CharField(max_length=32, choices=ACTION_CHOICES)
    value = models.CharField(max_length=64, blank=True, default="")
    notes = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        if self.value:
            return f"{self.bulb.name}: {self.action}={self.value} @ {self.created_at:%Y-%m-%d %H:%M:%S}"
        return f"{self.bulb.name}: {self.action} @ {self.created_at:%Y-%m-%d %H:%M:%S}"


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