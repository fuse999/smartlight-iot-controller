import zoneinfo

from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import UserCreationForm
from django.utils import timezone

from .models import BulbAccess, LightSchedule
from .services import compute_next_run

User = get_user_model()


class LightScheduleForm(forms.ModelForm):
    SCHEDULE_KIND_ONE_TIME = "one_time"
    SCHEDULE_KIND_WEEKLY = "weekly"

    schedule_kind = forms.ChoiceField(
        choices=[
            (SCHEDULE_KIND_ONE_TIME, "One-time"),
            (SCHEDULE_KIND_WEEKLY, "Weekly recurring"),
        ],
        initial=SCHEDULE_KIND_WEEKLY,
        widget=forms.RadioSelect,
    )
    timezone_name = forms.CharField(required=False, widget=forms.HiddenInput())

    class Meta:
        model = LightSchedule
        fields = [
            "name",
            "schedule_kind",
            "target_is_on",
            "target_brightness",
            "scheduled_for",
            "time_of_day",
            "monday",
            "tuesday",
            "wednesday",
            "thursday",
            "friday",
            "saturday",
            "sunday",
            "timezone_name",
            "enabled",
        ]
        widgets = {
            "name": forms.TextInput(attrs={"placeholder": "Morning lights, Porch off, etc."}),
            "target_brightness": forms.NumberInput(attrs={"min": 0, "max": 100, "step": 1}),
            "scheduled_for": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "time_of_day": forms.TimeInput(attrs={"type": "time"}),
        }

    def __init__(self, *args, bulb=None, request_tzname=None, **kwargs):
        self.bulb = bulb
        super().__init__(*args, **kwargs)

        tzname = request_tzname or timezone.get_current_timezone_name()
        if self.instance.pk:
            tzname = self.instance.timezone_name or tzname
            self.initial.setdefault(
                "schedule_kind",
                self.SCHEDULE_KIND_WEEKLY if self.instance.repeat else self.SCHEDULE_KIND_ONE_TIME,
            )
        self.initial.setdefault("timezone_name", tzname)
        self.fields["timezone_name"].initial = tzname

    def clean_timezone_name(self):
        tzname = (self.cleaned_data.get("timezone_name") or self.initial.get("timezone_name") or "UTC").strip()
        try:
            zoneinfo.ZoneInfo(tzname)
        except Exception as exc:
            raise forms.ValidationError("Invalid timezone.") from exc
        return tzname

    def _schedule_candidate_moments(self, schedule: LightSchedule, horizon_days: int = 14):
        end_horizon = timezone.now() + timezone.timedelta(days=horizon_days)
        cursor = timezone.now()
        candidates: list = []

        if schedule.repeat:
            while True:
                next_run = compute_next_run(schedule, from_dt=cursor)
                if next_run is None or next_run > end_horizon:
                    break
                candidates.append(next_run.astimezone(zoneinfo.ZoneInfo("UTC")).replace(second=0, microsecond=0))
                cursor = next_run + timezone.timedelta(seconds=1)
            return candidates

        if schedule.scheduled_for and schedule.scheduled_for > timezone.now():
            candidates.append(schedule.scheduled_for.astimezone(zoneinfo.ZoneInfo("UTC")).replace(second=0, microsecond=0))
        return candidates

    def _build_schedule_from_cleaned(self, cleaned) -> LightSchedule:
        return LightSchedule(
            bulb=self.bulb,
            target_is_on=cleaned.get("target_is_on"),
            target_brightness=cleaned.get("target_brightness"),
            repeat=cleaned.get("repeat", True),
            scheduled_for=cleaned.get("scheduled_for"),
            time_of_day=cleaned.get("time_of_day"),
            monday=cleaned.get("monday", False),
            tuesday=cleaned.get("tuesday", False),
            wednesday=cleaned.get("wednesday", False),
            thursday=cleaned.get("thursday", False),
            friday=cleaned.get("friday", False),
            saturday=cleaned.get("saturday", False),
            sunday=cleaned.get("sunday", False),
            timezone_name=cleaned.get("timezone_name") or "UTC",
            enabled=cleaned.get("enabled", True),
        )

    def _conflicting_schedules(self, cleaned):
        if self.bulb is None or not cleaned.get("enabled", True):
            return []

        new_schedule = self._build_schedule_from_cleaned(cleaned)
        new_candidates = set(self._schedule_candidate_moments(new_schedule))
        if not new_candidates:
            return []

        existing_qs = LightSchedule.objects.filter(bulb=self.bulb, enabled=True)
        if self.instance.pk:
            existing_qs = existing_qs.exclude(pk=self.instance.pk)

        conflicts = []
        for existing in existing_qs:
            existing_candidates = set(self._schedule_candidate_moments(existing))
            if new_candidates.intersection(existing_candidates):
                conflicts.append(existing)
        return conflicts

    def clean(self):
        cleaned = super().clean()

        schedule_kind = cleaned.get("schedule_kind")
        target_is_on = cleaned.get("target_is_on")
        target_brightness = cleaned.get("target_brightness")
        scheduled_for = cleaned.get("scheduled_for")
        time_of_day = cleaned.get("time_of_day")

        selected_days = [
            cleaned.get("monday"),
            cleaned.get("tuesday"),
            cleaned.get("wednesday"),
            cleaned.get("thursday"),
            cleaned.get("friday"),
            cleaned.get("saturday"),
            cleaned.get("sunday"),
        ]

        if target_is_on:
            if target_brightness in (None, ""):
                self.add_error("target_brightness", "Brightness is required when turning the light on.")
        else:
            if target_brightness not in (None, ""):
                self.add_error("target_brightness", "Brightness must be blank when the schedule turns the light off.")
            cleaned["target_brightness"] = None

        if schedule_kind == self.SCHEDULE_KIND_ONE_TIME:
            cleaned["repeat"] = False
            if not scheduled_for:
                self.add_error("scheduled_for", "Choose a specific date and time for a one-time schedule.")
            if time_of_day or any(selected_days):
                raise forms.ValidationError(
                    "Choose either a one-time schedule or a recurring weekly schedule, not both."
                )
            if scheduled_for and scheduled_for <= timezone.now():
                self.add_error("scheduled_for", "One-time schedules must be in the future.")

            cleaned["time_of_day"] = None
            for field_name in [
                "monday",
                "tuesday",
                "wednesday",
                "thursday",
                "friday",
                "saturday",
                "sunday",
            ]:
                cleaned[field_name] = False

        elif schedule_kind == self.SCHEDULE_KIND_WEEKLY:
            cleaned["repeat"] = True
            if scheduled_for:
                self.add_error("scheduled_for", "Clear the one-time date/time when creating a recurring schedule.")
            if not time_of_day:
                self.add_error("time_of_day", "Choose a recurring time of day.")
            if not any(selected_days):
                raise forms.ValidationError("Select at least one weekday for a recurring schedule.")
            cleaned["scheduled_for"] = None
        else:
            raise forms.ValidationError("Choose a valid schedule type.")

        if not self.errors:
            conflicts = self._conflicting_schedules(cleaned)
            if conflicts:
                labels = ", ".join(conflict.name or f"#{conflict.id}" for conflict in conflicts[:3])
                raise forms.ValidationError(
                    f"Another enabled schedule already targets this bulb at the same exact time. "
                    f"Conflicting schedule(s): {labels}. Edit or disable the existing schedule first."
                )

        return cleaned

    def save(self, commit=True):
        schedule = super().save(commit=False)
        schedule.repeat = bool(self.cleaned_data.get("repeat", True))
        schedule.timezone_name = self.cleaned_data.get("timezone_name") or timezone.get_current_timezone_name()

        if schedule.repeat:
            schedule.scheduled_for = None
        else:
            schedule.time_of_day = None
            schedule.monday = False
            schedule.tuesday = False
            schedule.wednesday = False
            schedule.thursday = False
            schedule.friday = False
            schedule.saturday = False
            schedule.sunday = False

        if not schedule.target_is_on:
            schedule.target_brightness = None

        if commit:
            schedule.save()

        return schedule


class AccessShareForm(forms.Form):
    identifier = forms.CharField(
        max_length=254,
        label="Username or Email",
        widget=forms.TextInput(
            attrs={
                "placeholder": "Enter a username or email address",
                "autocomplete": "off",
            }
        ),
    )
    role = forms.ChoiceField(choices=[])

    def __init__(self, *args, allowed_roles=None, **kwargs):
        super().__init__(*args, **kwargs)
        allowed_roles = allowed_roles or []
        label_map = dict(BulbAccess.ROLE_CHOICES)
        self.fields["role"].choices = [(role, label_map.get(role, role.title())) for role in allowed_roles]

    def clean_identifier(self):
        return self.cleaned_data["identifier"].strip()


class RegisterForm(UserCreationForm):
    class Meta:
        model = User
        fields = ["username", "password1", "password2"]
