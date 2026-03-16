from django import forms
from django.utils import timezone
from .models import LightSchedule

from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User


class LightScheduleForm(forms.ModelForm):
    class Meta:
        model = LightSchedule
        fields = [
            "run_at",
            "target_is_on",
            "target_brightness",
            "enabled",
        ]
        widgets = {
            "run_at": forms.DateTimeInput(attrs={"type": "hidden"}),
        }

    def clean_run_at(self):
        run_at = self.cleaned_data["run_at"]

        if timezone.is_naive(run_at):
            run_at = timezone.make_aware(run_at, timezone.utc)

        if run_at <= timezone.now() + timezone.timedelta(seconds=10):
            raise forms.ValidationError("Scheduled time must be in the future.")

        return run_at

    def clean(self):
        cleaned = super().clean()
        target_is_on = cleaned.get("target_is_on")
        target_brightness = cleaned.get("target_brightness")

        if target_is_on is False and target_brightness is not None:
            cleaned["target_brightness"] = None

        return cleaned


class RegisterForm(UserCreationForm):
    class Meta:
        model = User
        fields = ["username", "password1", "password2"]