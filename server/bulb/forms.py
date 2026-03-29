from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User

from .models import LightSchedule


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

        target_is_on = cleaned.get("target_is_on")
        target_brightness = cleaned.get("target_brightness")
        time_of_day = cleaned.get("time_of_day")

        if target_is_on is False:
            cleaned["target_brightness"] = None

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

        if not time_of_day:
            self.add_error("time_of_day", "Please choose a time.")

        if not any(day_fields):
            raise forms.ValidationError("Please select at least one weekday.")

        return cleaned

    def save(self, commit=True):
        schedule = super().save(commit=False)
        schedule.repeat = True
        schedule.scheduled_for = None

        if commit:
            schedule.save()

        return schedule


class RegisterForm(UserCreationForm):
    class Meta:
        model = User
        fields = ["username", "password1", "password2"]