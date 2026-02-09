import json

from django.conf import settings
from django.http import JsonResponse, HttpResponseForbidden, HttpResponseBadRequest
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST
from django.views.decorators.csrf import csrf_exempt

from .models import LightState


def _is_authorized(request) -> bool:
    """Simple device token auth: Authorization: Bearer <token>"""
    token = getattr(settings, "DEVICE_API_TOKEN", "")
    auth = request.headers.get("Authorization", "")
    return bool(token) and auth == f"Bearer {token}"


def _get_state() -> LightState:
    # Keep a single row; create if missing
    state = LightState.objects.order_by("id").first()
    if state is None:
        state = LightState.objects.create(is_on=False, brightness=100, updated_at=timezone.now())
    return state


@require_GET
@csrf_exempt
def desired_state(request):
    if not _is_authorized(request):
        return HttpResponseForbidden("Bad token")

    state = _get_state()
    return JsonResponse({
        "is_on": state.is_on,
        "brightness": state.brightness,
        "updated_at": state.updated_at.isoformat(),
    })


@require_POST
@csrf_exempt
def report_state(request):
    if not _is_authorized(request):
        return HttpResponseForbidden("Bad token")

    try:
        body = request.body.decode("utf-8")
        data = json.loads(body) if body else {}
    except Exception:
        return HttpResponseBadRequest("Invalid JSON")

    return JsonResponse({"ok": True})
