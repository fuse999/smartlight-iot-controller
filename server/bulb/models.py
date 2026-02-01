from django.db import models
from django.utils import timezone


class LightState(models.Model):
    """
    Stores the current ON/OFF state of the bulb.
    We'll keep this as a single row by always using get_or_create(id=1).
    """
    is_on = models.BooleanField(default=False)
    updated_at = models.DateTimeField(default=timezone.now)

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
    ]

    action = models.CharField(max_length=8, choices=ACTION_CHOICES)
    created_at = models.DateTimeField(default=timezone.now)

    def __str__(self) -> str:
        return f"{self.action} @ {self.created_at:%Y-%m-%d %H:%M:%S}"
