from django.db import models
from django.utils import timezone
from django.core.validators import MaxValueValidator, MinValueValidator


class LightState(models.Model):
    is_on = models.BooleanField(default=False)
    brightness = models.PositiveSmallIntegerField(
        default=100,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    updated_at = models.DateTimeField(default=timezone.now)

    class Meta:
        permissions = [
            ("can_control_bulb", "Can control the light bulb"),
        ]

    def __str__(self) -> str:
        return "ON" if self.is_on else "OFF"


class ControlActivity(models.Model):
    ACTION_CHOICES = [
        ("ON", "Turn ON"),
        ("OFF", "Turn OFF"),
        ("BRIGHTNESS", "Set Brightness"),
    ]

    action = models.CharField(max_length=16, choices=ACTION_CHOICES)
    value = models.CharField(max_length=32, blank=True, default="")
    created_at = models.DateTimeField(default=timezone.now)

    def __str__(self) -> str:
        if self.action == "BRIGHTNESS" and self.value:
            return f"{self.action}={self.value} @ {self.created_at:%Y-%m-%d %H:%M:%S}"
        return f"{self.action} @ {self.created_at:%Y-%m-%d %H:%M:%S}"


class LightSchedule(models.Model):
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

    monday = models.BooleanField(default=False)
    tuesday = models.BooleanField(default=False)
    wednesday = models.BooleanField(default=False)
    thursday = models.BooleanField(default=False)
    friday = models.BooleanField(default=False)
    saturday = models.BooleanField(default=False)
    sunday = models.BooleanField(default=False)

    # Time chosen by the user for this schedule.
    time_of_day = models.TimeField()

    # The timezone this schedule should always obey.
    # Store the browser/session timezone at creation time.
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
        return (
            f"{'ENABLED' if self.enabled else 'DISABLED'} "
            f"[{self.days_display()} @ {self.time_of_day:%H:%M} {self.timezone_name}] "
            f"-> on={self.target_is_on}{bri}"
        )