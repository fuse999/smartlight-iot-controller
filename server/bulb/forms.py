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
        widgets = {
            # Makes the input render as a native datetime picker in most browsers
            "run_at": forms.DateTimeInput(attrs={"type": "datetime-local"}),
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
            target_is_on = cleaned.get("target_is_on")
            target_brightness = cleaned.get("target_brightness")

            # If turning OFF, brightness should not be set (avoid ambiguity).
            if target_is_on is False and target_brightness is not None:
                cleaned["target_brightness"] = None
            # If turning ON, brightness should be set.
            if target_is_on is True and target_brightness is None:
                 cleaned["target_brightness"] = 0

            return cleaned