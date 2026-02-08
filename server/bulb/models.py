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

class LightSchedule(models.Model):
    run_at = models.DateTimeField(db_index=True)
    enabled = models.BooleanField(default=True) #for disabling schedules without completely removing them
    created_at = models.DateTimeField(default=timezone.now)
    executed_at = models.DateTimeField(null=True, blank=True)

    target_is_on = models.BooleanField() #True for the light turning on
    target_brightness = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(0), MaxValueValidator(100)],
        null=True,
        blank=True,
    )

    class Meta:
        ordering = ["run_at"]
    
    def __str__(self) -> str:
        bri = "" if self.target_brightness is None else f", bri={self.target_brightness}"
        return f"{'ENABLED' if self.enabled else 'DISABLED'} @ {self.run_at:%Y-%m-%d %H:%M:%S} -> on={self.target_is_on}{bri}"
    


    
