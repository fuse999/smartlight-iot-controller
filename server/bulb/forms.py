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
            "repeat",
            "scheduled_for",
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
            "scheduled_for": "Schedule For",
            "time_of_day": "Time of Day",
            "enabled": "Schedule Enabled",
        }
        widgets = {
            "scheduled_for": forms.DateTimeInput(
                attrs={"type": "datetime-local", "class": "form-control"},
                format="%Y-%m-%dT%H:%M",
            ),
            "time_of_day": forms.TimeInput(attrs={"type": "time"}),
        }

        
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["scheduled_for"].input_formats = ["%Y-%m-%dT%H:%M"]

    def clean(self):
        cleaned = super().clean()

        repeat = cleaned.get("repeat")
        scheduled_for = cleaned.get("scheduled_for")
        time_of_day = cleaned.get("time_of_day")

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

        if repeat:
            if not time_of_day:
                self.add_error("time_of_day", "Please choose a time.")
            if not any(day_fields):
                raise forms.ValidationError("Please select at least one weekday for repeating schedules.")
            cleaned["scheduled_for"] = None
        else:
            if not scheduled_for:
                self.add_error("scheduled_for", "Please choose a date and time.")
            cleaned["time_of_day"] = None
            cleaned["monday"] = False
            cleaned["tuesday"] = False
            cleaned["wednesday"] = False
            cleaned["thursday"] = False
            cleaned["friday"] = False
            cleaned["saturday"] = False
            cleaned["sunday"] = False

        return cleaned


class RegisterForm(UserCreationForm):
    class Meta:
        model = User
        fields = ["username", "password1", "password2"]