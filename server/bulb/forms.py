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
        widgets = {
            "time_of_day": forms.TimeInput(attrs={"type": "time"}),
        }

    def clean(self):
        cleaned = super().clean()

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