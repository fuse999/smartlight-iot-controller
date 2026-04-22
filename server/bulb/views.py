import json

from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, HttpResponseBadRequest, HttpResponseForbidden
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_POST

from .forms import LightScheduleForm, RegisterForm
from .models import Bulb, BulbAccess, LightSchedule
from .services import (
    DEVICE_OFFLINE_AFTER_SECONDS,
    get_account_power_summary,
    get_bulb_power_summary,
    refresh_bulb_online_statuses,
    refresh_next_run,
    set_bulb_brightness,
    set_bulb_power,
    user_can_control_bulb,
    user_can_manage_bulb,
    user_can_view_bulb,
)


# ---------------------------------------------------------
# Helper: Return bulbs this user is allowed to access
# ---------------------------------------------------------
def _accessible_bulbs_for_user(user):
    # Refresh online/offline flags before showing bulb data.
    refresh_bulb_online_statuses()

    if not user.is_authenticated:
        return Bulb.objects.none()

    if user.is_superuser:
        return Bulb.objects.all().order_by("name", "id")

    owned = Bulb.objects.filter(owner=user)
    shared = Bulb.objects.filter(
        user_access__user=user,
        user_access__role__in=[
            BulbAccess.ROLE_OWNER,
            BulbAccess.ROLE_ADMIN,
            BulbAccess.ROLE_CONTROLLER,
            BulbAccess.ROLE_VIEWER,
        ],
    )

    return (owned | shared).distinct().select_related("owner").order_by("name", "id")


# ---------------------------------------------------------
# Helper: Pick selected bulb from query string or default
# ---------------------------------------------------------
def _selected_bulb_for_request(request):
    bulbs = _accessible_bulbs_for_user(request.user)
    if not request.user.is_authenticated or not bulbs.exists():
        return None

    bulb_id = request.GET.get("bulb")
    if bulb_id:
        try:
            return bulbs.get(id=bulb_id)
        except Bulb.DoesNotExist:
            pass

    return bulbs.first()


# ---------------------------------------------------------
# Helper: Return schedules for one bulb
# ---------------------------------------------------------
def _schedule_queryset_for_bulb(bulb):
    if bulb is None:
        return LightSchedule.objects.none()
    return bulb.schedules.order_by("next_run_at", "id")


# ---------------------------------------------------------
# Page: Landing page
# ---------------------------------------------------------
@ensure_csrf_cookie
def home_view(request):
    return render(request, "home.html")


# ---------------------------------------------------------
# Page: Main bulb dashboard
# ---------------------------------------------------------
@ensure_csrf_cookie
def dashboard_page(request):
    bulbs = list(_accessible_bulbs_for_user(request.user))
    bulb = _selected_bulb_for_request(request)

    can_view = bulb is not None and user_can_view_bulb(request.user, bulb)
    can_control = bulb is not None and user_can_control_bulb(request.user, bulb)
    can_manage = bulb is not None and user_can_manage_bulb(request.user, bulb)

    # Attach a power summary for the selected bulb only.
    selected_bulb_power_summary = None
    if bulb is not None and can_view:
        selected_bulb_power_summary = get_bulb_power_summary(bulb)

    # Handle schedule creation from the dashboard form.
    if request.method == "POST":
        if not can_manage or bulb is None:
            return redirect("bulb_dashboard")

        form = LightScheduleForm(request.POST)
        if form.is_valid():
            schedule = form.save(commit=False)
            schedule.bulb = bulb
            schedule.created_by = request.user if request.user.is_authenticated else None
            schedule.claimed_at = None
            schedule.last_run_at = None
            schedule.timezone_name = timezone.get_current_timezone_name()
            schedule.save()
            refresh_next_run(schedule)
            return redirect(f"{request.path}?bulb={bulb.id}")
    else:
        form = LightScheduleForm()

    schedules = _schedule_queryset_for_bulb(bulb) if can_view else LightSchedule.objects.none()

    return render(
        request,
        "bulb/dashboard.html",
        {
            "state": bulb,
            "selected_bulb": bulb,
            "selected_bulb_power_summary": selected_bulb_power_summary,
            "bulbs": bulbs,
            "form": form,
            "schedules": schedules,
            "register_form": RegisterForm(),
            "can_control": can_control,
            "can_manage": can_manage,
            "can_view": can_view,
        },
    )


# ---------------------------------------------------------
# Page: Show bulbs linked to the current user
# ---------------------------------------------------------
@ensure_csrf_cookie
def my_bulbs_page(request):
    bulbs = list(_accessible_bulbs_for_user(request.user))
    bulb = _selected_bulb_for_request(request)

    # Attach per-bulb summaries so the template can show them directly.
    for bulb_item in bulbs:
        bulb_item.power_summary = get_bulb_power_summary(bulb_item)

    account_power_summary = get_account_power_summary(bulbs)

    return render(
        request,
        "bulb/my_bulbs.html",
        {
            "selected_bulb": bulb,
            "bulbs": bulbs,
            "account_power_summary": account_power_summary,
            "can_view": bulb is not None and user_can_view_bulb(request.user, bulb),
            "can_control": bulb is not None and user_can_control_bulb(request.user, bulb),
            "can_manage": bulb is not None and user_can_manage_bulb(request.user, bulb),
        },
    )


# ---------------------------------------------------------
# Page: Show claimable bulbs still in pairing mode
# ---------------------------------------------------------
@login_required
def claim_bulb_page(request):
    # Refresh online/offline flags before showing claimable devices.
    refresh_bulb_online_statuses()

    now = timezone.now()
    online_cutoff = now - timezone.timedelta(seconds=DEVICE_OFFLINE_AFTER_SECONDS)

    claimable_bulbs = Bulb.objects.filter(
        owner__isnull=True,
        pairing_mode_enabled=True,
        pairing_expires_at__gt=now,
        last_seen_at__gte=online_cutoff,
        is_active=True,
        is_online=True,
    ).order_by("-last_seen_at", "name")

    return render(
        request,
        "bulb/claim_bulb.html",
        {
            "claimable_bulbs": claimable_bulbs,
        },
    )


# ---------------------------------------------------------
# Action: Claim a bulb for the current user
# ---------------------------------------------------------
@login_required
@require_POST
def claim_bulb_action(request, bulb_id):
    bulb = get_object_or_404(
        Bulb,
        id=bulb_id,
        owner__isnull=True,
        pairing_mode_enabled=True,
        pairing_expires_at__gt=timezone.now(),
        is_active=True,
    )

    bulb.owner = request.user
    bulb.pairing_mode_enabled = False
    bulb.pairing_expires_at = None
    bulb.claimed_at = timezone.now()
    bulb.save(update_fields=[
        "owner",
        "pairing_mode_enabled",
        "pairing_expires_at",
        "claimed_at",
    ])

    BulbAccess.objects.update_or_create(
        user=request.user,
        bulb=bulb,
        defaults={"role": BulbAccess.ROLE_OWNER},
    )

    messages.success(request, f'"{bulb.name}" is now linked to your account.')
    return redirect(f"/dashboard/?bulb={bulb.id}")


# ---------------------------------------------------------
# Action: Remove bulb from current user account
# ---------------------------------------------------------
@login_required
@require_POST
def unclaim_bulb_action(request, bulb_id):
    bulb = get_object_or_404(Bulb, id=bulb_id, is_active=True)

    if bulb.owner_id != request.user.id and not request.user.is_superuser:
        return HttpResponseForbidden("You do not have permission to unclaim this device.")

    bulb.owner = None
    bulb.claimed_at = None
    bulb.pairing_mode_enabled = True
    bulb.pairing_expires_at = timezone.now() + timezone.timedelta(minutes=2)
    bulb.save(update_fields=[
        "owner",
        "claimed_at",
        "pairing_mode_enabled",
        "pairing_expires_at",
    ])

    BulbAccess.objects.filter(bulb=bulb).delete()

    messages.success(
        request,
        f'"{bulb.name}" has been removed from your account and is claimable again.'
    )
    return redirect("my_bulbs")


# ---------------------------------------------------------
# Page: Manage schedules for a selected bulb
# ---------------------------------------------------------
@ensure_csrf_cookie
def schedules_page(request):
    bulbs = list(_accessible_bulbs_for_user(request.user))
    bulb = _selected_bulb_for_request(request)

    can_view = bulb is not None and user_can_view_bulb(request.user, bulb)
    can_manage = bulb is not None and user_can_manage_bulb(request.user, bulb)

    # Handle schedule creation from schedules page.
    if request.method == "POST":
        if not can_manage or bulb is None:
            return redirect("bulb_schedules")

        form = LightScheduleForm(request.POST)
        if form.is_valid():
            schedule = form.save(commit=False)
            schedule.bulb = bulb
            schedule.created_by = request.user if request.user.is_authenticated else None
            schedule.claimed_at = None
            schedule.last_run_at = None
            schedule.timezone_name = timezone.get_current_timezone_name()
            schedule.save()
            refresh_next_run(schedule)
            return redirect(f"{request.path}?bulb={bulb.id}")
    else:
        form = LightScheduleForm()

    schedules = _schedule_queryset_for_bulb(bulb) if can_view else LightSchedule.objects.none()

    return render(
        request,
        "bulb/schedules.html",
        {
            "form": form,
            "schedules": schedules,
            "selected_bulb": bulb,
            "bulbs": bulbs,
            "can_control": can_manage,
            "can_manage": can_manage,
            "can_view": can_view,
        },
    )


# ---------------------------------------------------------
# API: Turn selected bulb on or off
# ---------------------------------------------------------
@require_POST
def set_power_api(request):
    bulb = _selected_bulb_for_request(request)
    if bulb is None:
        return HttpResponseForbidden("No bulb available.")

    if not user_can_control_bulb(request.user, bulb):
        return HttpResponseForbidden("You do not have permission to control this bulb.")

    try:
        payload = json.loads(request.body.decode("utf-8"))
        on = bool(payload["on"])
    except Exception:
        return HttpResponseBadRequest("Invalid JSON payload. Expected: {'on': true/false}")

    bulb = set_bulb_power(bulb, on, acted_by=request.user)

    return JsonResponse(
        {
            "ok": True,
            "bulb_id": bulb.id,
            "is_on": bulb.is_on,
            "updated_at": bulb.updated_at.isoformat() if bulb.updated_at else None,
        }
    )


# ---------------------------------------------------------
# API: Set selected bulb brightness
# ---------------------------------------------------------
@require_POST
def set_brightness_api(request):
    bulb = _selected_bulb_for_request(request)
    if bulb is None:
        return HttpResponseForbidden("No bulb available.")

    if not user_can_control_bulb(request.user, bulb):
        return HttpResponseForbidden("You do not have permission to control this bulb.")

    try:
        payload = json.loads(request.body.decode("utf-8"))
        brightness = int(payload["brightness"])
    except Exception:
        return HttpResponseBadRequest("Invalid JSON payload. Expected: {'brightness': 0-100}")

    bulb = set_bulb_brightness(bulb, brightness, acted_by=request.user)

    return JsonResponse(
        {
            "ok": True,
            "bulb_id": bulb.id,
            "brightness": bulb.brightness,
            "updated_at": bulb.updated_at.isoformat() if bulb.updated_at else None,
        }
    )


# ---------------------------------------------------------
# API: Return current state of selected bulb
# ---------------------------------------------------------
def light_state_api(request):
    bulb = _selected_bulb_for_request(request)
    if bulb is None:
        return JsonResponse(
            {
                "is_on": False,
                "brightness": 100,
                "updated_at": None,
                "bulb_id": None,
            }
        )

    if not user_can_view_bulb(request.user, bulb):
        return HttpResponseForbidden("You do not have permission to view this bulb.")

    return JsonResponse(
        {
            "bulb_id": bulb.id,
            "is_on": bulb.is_on,
            "brightness": bulb.brightness,
            "updated_at": bulb.updated_at.isoformat() if bulb.updated_at else None,
        }
    )


# ---------------------------------------------------------
# API: Return schedule information for selected bulb
# ---------------------------------------------------------
def schedule_status_api(request):
    bulb = _selected_bulb_for_request(request)

    if bulb is None or not user_can_view_bulb(request.user, bulb):
        return JsonResponse({"schedules": []})

    schedules = _schedule_queryset_for_bulb(bulb)

    data = []
    for s in schedules:
        data.append(
            {
                "id": s.id,
                "name": s.name or "—",
                "enabled": s.enabled,
                "days_display": s.days_display(),
                "time_of_day": s.time_of_day.strftime("%I:%M %p").lstrip("0") if s.time_of_day else "—",
                "timezone_name": s.timezone_name or "—",
                "target_is_on": s.target_is_on,
                "target_brightness": s.target_brightness,
                "next_run_at": s.next_run_at.isoformat() if s.next_run_at else None,
                "last_run_at": s.last_run_at.isoformat() if s.last_run_at else None,
            }
        )

    return JsonResponse({"schedules": data})


def bulb_status_api(request):
    bulbs = _accessible_bulbs_for_user(request.user)

    data = []
    for bulb in bulbs:
        data.append({
            "id": bulb.id,
            "name": bulb.name,
            "is_online": bulb.is_online,
            "last_seen_display": bulb.last_seen_at.strftime("%Y-%m-%d %I:%M %p") if bulb.last_seen_at else "Never",
        })

    return JsonResponse({"bulbs": data})

# ---------------------------------------------------------
# Action: Enable or disable a schedule
# ---------------------------------------------------------
@require_POST
def toggle_schedule(request, schedule_id):
    schedule = get_object_or_404(LightSchedule, id=schedule_id)
    bulb = schedule.bulb

    if bulb is None or not user_can_manage_bulb(request.user, bulb):
        return HttpResponseForbidden("You do not have permission to manage this bulb's schedules.")

    schedule.enabled = not schedule.enabled

    if schedule.enabled:
        schedule.claimed_at = None
        schedule.save(update_fields=["enabled", "claimed_at"])
        refresh_next_run(schedule)
    else:
        schedule.claimed_at = None
        schedule.save(update_fields=["enabled", "claimed_at"])

    next_url = request.POST.get("next")
    if next_url:
        return redirect(next_url)
    return redirect(f"/schedules/?bulb={bulb.id}")


# ---------------------------------------------------------
# Action: Delete a schedule
# ---------------------------------------------------------
@require_POST
def delete_schedule(request, schedule_id):
    schedule = get_object_or_404(LightSchedule, id=schedule_id)
    bulb = schedule.bulb

    if bulb is None or not user_can_manage_bulb(request.user, bulb):
        return HttpResponseForbidden("You do not have permission to manage this bulb's schedules.")

    schedule.delete()

    next_url = request.POST.get("next")
    if next_url:
        return redirect(next_url)
    return redirect(f"/schedules/?bulb={bulb.id}")


# ---------------------------------------------------------
# API: Save browser timezone into session
# ---------------------------------------------------------
@require_POST
def set_timezone_api(request):
    try:
        payload = json.loads(request.body.decode("utf-8"))
        tzname = payload["timezone"]
    except Exception:
        return HttpResponseBadRequest("Invalid JSON payload. Expected: {'timezone': 'Area/City'}")

    request.session["django_timezone"] = tzname

    return JsonResponse(
        {
            "ok": True,
            "timezone": tzname,
        }
    )


# ---------------------------------------------------------
# Page: Register a new user account
# ---------------------------------------------------------
def register_view(request):
    if request.method == "POST":
        register_form = RegisterForm(request.POST)

        if register_form.is_valid():
            user = register_form.save()
            login(request, user)

            next_url = request.POST.get("next") or "/dashboard/"
            return redirect(next_url)
    else:
        register_form = RegisterForm()

    return render(
        request,
        "registration/register.html",
        {
            "register_form": register_form,
        },
    )