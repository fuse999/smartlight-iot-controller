import json

from django.http import JsonResponse, HttpResponseBadRequest, HttpResponseForbidden
from django.shortcuts import render, redirect, get_object_or_404
from django.views.decorators.http import require_POST
from django.views.decorators.csrf import csrf_protect, ensure_csrf_cookie
from django.contrib.auth.decorators import permission_required

from django.contrib.auth import login
from django.contrib.auth.models import Permission

from .forms import LightScheduleForm, RegisterForm
from .models import LightSchedule
from .services import get_state, set_light, set_brightness


def home_view(request):
    return render(request, "home.html")


@permission_required("bulb.can_control_bulb", raise_exception=True)
def control_page(request):
    state = get_state()
    return render(request, "bulb/control.html", {"state": state})


@permission_required("bulb.can_control_bulb", raise_exception=True)
@csrf_protect
@require_POST
def set_power_api(request):
    try:
        payload = json.loads(request.body.decode("utf-8"))
        on = bool(payload["on"])
    except Exception:
        return HttpResponseBadRequest("Invalid JSON payload. Expected: {'on': true/false}")

    state = set_light(on)
    return JsonResponse({
        "ok": True,
        "is_on": state.is_on,
        "updated_at": state.updated_at.isoformat(),
    })


@permission_required("bulb.can_control_bulb", raise_exception=True)
@csrf_protect
@require_POST
def set_brightness_api(request):
    try:
        payload = json.loads(request.body.decode("utf-8"))
        brightness = int(payload["brightness"])
    except Exception:
        return HttpResponseBadRequest("Invalid JSON payload. Expected: {'brightness': 0-100}")

    state = set_brightness(brightness)
    return JsonResponse({
        "ok": True,
        "brightness": state.brightness,
        "updated_at": state.updated_at.isoformat(),
    })


@permission_required("bulb.can_control_bulb", raise_exception=True)
@csrf_protect
def schedules_page(request):
    if request.method == "POST":
        form = LightScheduleForm(request.POST)
        if form.is_valid():
            form.save()
            return redirect("bulb_schedules")
    else:
        form = LightScheduleForm()

    schedules = LightSchedule.objects.order_by("run_at")
    return render(request, "bulb/schedules.html", {
        "form": form,
        "schedules": schedules,
    })


@permission_required("bulb.can_control_bulb", raise_exception=True)
@ensure_csrf_cookie
@csrf_protect
def dashboard_page(request):
    state = get_state()

    if request.method == "POST":
        form = LightScheduleForm(request.POST)
        if form.is_valid():
            form.save()
            return redirect("bulb_dashboard")
    else:
        form = LightScheduleForm()

    schedules = LightSchedule.objects.order_by("run_at")

    return render(request, "bulb/dashboard.html", {
        "state": state,
        "form": form,
        "schedules": schedules,
        "register_form": RegisterForm(),
    })


@permission_required("bulb.can_control_bulb", raise_exception=True)
@require_POST
@csrf_protect
def toggle_schedule(request, schedule_id):
    schedule = get_object_or_404(LightSchedule, id=schedule_id)

    # Optional guard: don't let already executed schedules be re-enabled
    if schedule.executed_at is not None and not schedule.enabled:
        return redirect(request.POST.get("next") or "bulb_dashboard")

    schedule.enabled = not schedule.enabled
    schedule.save(update_fields=["enabled"])

    return redirect(request.POST.get("next") or "bulb_dashboard")


@permission_required("bulb.can_control_bulb", raise_exception=True)
@require_POST
@csrf_protect
def delete_schedule(request, schedule_id):
    schedule = get_object_or_404(LightSchedule, id=schedule_id)
    schedule.delete()
    return redirect(request.POST.get("next") or "bulb_dashboard")


@require_POST
@csrf_protect
def set_timezone_api(request):
    try:
        payload = json.loads(request.body.decode("utf-8"))
        tzname = payload["timezone"]
    except Exception:
        return HttpResponseBadRequest("Invalid JSON payload. Expected: {'timezone': 'Area/City'}")

    request.session["django_timezone"] = tzname
    return JsonResponse({
        "ok": True,
        "timezone": tzname,
    })


@csrf_protect
def register_view(request):
    if request.method != "POST":
        return redirect("bulb_dashboard")

    register_form = RegisterForm(request.POST)

    if register_form.is_valid():
        user = register_form.save()

        try:
            perm = Permission.objects.get(codename="can_control_bulb")
            user.user_permissions.add(perm)
        except Permission.DoesNotExist:
            pass

        login(request, user)
        next_url = request.POST.get("next") or "/dashboard/"
        return redirect(next_url)

    state = get_state()
    schedules = LightSchedule.objects.order_by("run_at")

    return render(request, "bulb/dashboard.html", {
        "state": state,
        "form": LightScheduleForm(),
        "schedules": schedules,
        "register_form": register_form,
        "open_register_modal": True,
    })