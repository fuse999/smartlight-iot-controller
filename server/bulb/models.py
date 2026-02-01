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
    """
    Minimal activity log for manual controls.
    Later we can add user, source (manual/schedule), metadata, etc.
    """
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
