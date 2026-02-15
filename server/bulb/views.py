import json

from django.http import JsonResponse, HttpResponseBadRequest
from django.shortcuts import render, redirect
from django.views.decorators.http import require_POST
from django.views.decorators.csrf import csrf_protect
from django.contrib.auth.decorators import login_required, permission_required
from .forms import LightScheduleForm
from .models import LightSchedule


from .services import get_state, set_light, set_brightness


def home_view(request):
    return render(request, 'home.html')

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
    return render(request, "bulb/schedules.html", {"form": form, "schedules": schedules})
