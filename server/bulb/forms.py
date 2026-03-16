from django import forms
from .models import LightSchedule

from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User


class LightScheduleForm(forms.ModelForm):
    class Meta:
        model = LightSchedule
        fields = [
            "name",
            "target_is_on",
            "target_brightness",
            "time_of_day",
            "monday",
            "tuesday",
            "wednesday",
            "thursday",
            "friday",
            "saturday",
            "sunday",
            "enabled",
        ]
        labels = {
            "target_is_on": "Turn light on",
            "target_brightness": "Brightness",
            "enabled": "Schedule enabled",
        }
        widgets = {
            "time_of_day": forms.TimeInput(attrs={"type": "time"}),
        }

    def clean_run_at(self):
        run_at = self.cleaned_data["run_at"]

        # Convert naive -> aware using Django's current timezone
        if timezone.is_naive(run_at):
            run_at = timezone.make_aware(run_at, timezone.get_current_timezone())

        # small buffer so "right now" doesn't fail due to seconds
        if run_at <= timezone.now() + timezone.timedelta(seconds=10):
            raise forms.ValidationError("Scheduled time must be in the future.")
        
        if run_at and LightSchedule.objects.filter(run_at=run_at).exists():
             self.add_error(
                  "run_at",
                  "A schedule already exists for that date/time. Please choose a different one."
             )

        return run_at

    def clean(self):
        cleaned = super().clean()

            # If turning OFF, brightness should not be set (avoid ambiguity).
            if target_is_on is False and target_brightness is not None:
                cleaned["target_brightness"] = None
            # If turning ON, brightness should be set.
            if target_is_on is True and target_brightness is None:
                 cleaned["target_brightness"] = 0
        day_fields = [
            cleaned.get("monday"),
            cleaned.get("tuesday"),
            cleaned.get("wednesday"),
            cleaned.get("thursday"),
            cleaned.get("friday"),
            cleaned.get("saturday"),
            cleaned.get("sunday"),
        ]

        if not any(day_fields):
            raise forms.ValidationError("Please select at least one day of the week.")

        target_is_on = cleaned.get("target_is_on")
        target_brightness = cleaned.get("target_brightness")

        if target_is_on is False and target_brightness is not None:
            cleaned["target_brightness"] = None

        return cleaned


class RegisterForm(UserCreationForm):
    class Meta:
        model = User
        fields = ["username", "password1", "password2"]