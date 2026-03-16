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
            "target_is_on": "Turn Light On",
            "target_brightness": "Brightness",
            "enabled": "Schedule Enabled",
        }
        widgets = {
            "time_of_day": forms.TimeInput(attrs={"type": "time"}),
        }

    def clean(self):
        cleaned = super().clean()

        target_is_on = cleaned.get("target_is_on")
        target_brightness = cleaned.get("target_brightness")

        # If turning OFF, brightness should not be set
        if target_is_on is False:
            cleaned["target_brightness"] = None

        # If turning ON but brightness missing, default to 0
        if target_is_on is True and target_brightness is None:
            cleaned["target_brightness"] = 0

        # Ensure at least one day is selected
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

        return cleaned


class RegisterForm(UserCreationForm):
    class Meta:
        model = User
        fields = ["username", "password1", "password2"]