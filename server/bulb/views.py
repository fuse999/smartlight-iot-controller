import json

from django.http import JsonResponse, HttpResponseBadRequest
from django.shortcuts import render
from django.views.decorators.http import require_POST
from django.views.decorators.csrf import csrf_protect

from .services import get_state, set_light


def control_page(request):
    state = get_state()
    return render(request, "bulb/control.html", {"state": state})


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