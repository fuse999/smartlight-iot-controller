from django import forms
from django.utils import timezone
from .models import LightSchedule

class LightScheduleForm(forms.ModelForm):
    class Meta:
        model = LightSchedule
        fields = [
            "run_at",
            "target_is_on",
            "target_brightness",
            "enabled",
        ]

    def clean_run_at(self):
        run_at = self.cleaned_data["run_at"]
        if run_at <= timezone.now():
            raise forms.ValidationError("Scheduled time must be in the future.")
        return run_at
