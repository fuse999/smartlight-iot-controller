from datetime import timedelta
import json

from django.http import JsonResponse, HttpResponseBadRequest, HttpResponseForbidden
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from .models import Bulb, ControlActivity
from .services import request_control_action


def _extract_bearer_token(request):
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    return auth.split(" ", 1)[1].strip()


def _authenticate_device(request):
    token = _extract_bearer_token(request)
    if not token:
        return None

    try:
        return Bulb.objects.get(device_token=token, is_active=True)
    except Bulb.DoesNotExist:
        return None


@csrf_exempt
@require_POST
def register_device(request):
    token = _extract_bearer_token(request)
    if not token:
        return HttpResponseForbidden("Invalid or missing device token.")

    try:
        payload = json.loads(request.body.decode("utf-8")) if request.body else {}
    except Exception:
        return HttpResponseBadRequest("Invalid JSON payload.")

    device_id = (payload.get("device_id") or "").strip()
    name = (payload.get("name") or "Unclaimed Smart Light Controller").strip()
    firmware_version = (payload.get("firmware_version") or "").strip()

    now = timezone.now()
    pairing_expires_at = now + timedelta(minutes=2)

    bulb, created = Bulb.objects.get_or_create(
        device_token=token,
        defaults={
            "name": name,
            "firmware_version": firmware_version,
            "is_active": True,
            "is_online": True,
            "last_seen_at": now,
            "pairing_mode_enabled": True,
            "pairing_expires_at": pairing_expires_at,
        },
    )

    fields_to_update = []

    if not created:
        if bulb.owner_id is None and name and bulb.name != name:
            bulb.name = name
            fields_to_update.append("name")

        if firmware_version and bulb.firmware_version != firmware_version:
            bulb.firmware_version = firmware_version
            fields_to_update.append("firmware_version")

        bulb.is_online = True
        bulb.last_seen_at = now
        fields_to_update.extend(["is_online", "last_seen_at"])

        if bulb.owner_id is None:
            bulb.pairing_mode_enabled = True
            bulb.pairing_expires_at = pairing_expires_at
            fields_to_update.extend(["pairing_mode_enabled", "pairing_expires_at"])

        if fields_to_update:
            bulb.save(update_fields=list(dict.fromkeys(fields_to_update)))

    if created:
        request_control_action(
            bulb=bulb,
            action=ControlActivity.ACTION_DEVICE_REPORTED,
            requested_is_on=bulb.is_on,
            requested_brightness=bulb.brightness,
            source_type=ControlActivity.SOURCE_DEVICE_SYNC,
            notes=f"Device auto-registered with server. device_id={device_id or 'registered'}",
        )

    return JsonResponse(
        {
            "ok": True,
            "created": created,
            "bulb_id": bulb.id,
            "bulb_uuid": str(bulb.uuid),
            "name": bulb.name,
            "claimed": bulb.owner_id is not None,
            "pairing_mode_enabled": bulb.pairing_mode_enabled,
            "pairing_expires_at": bulb.pairing_expires_at.isoformat() if bulb.pairing_expires_at else None,
        }
    )


@csrf_exempt
@require_GET
def desired_state(request):
    bulb = _authenticate_device(request)
    if bulb is None:
        return HttpResponseForbidden("Invalid or missing device token.")

    bulb.is_online = True
    bulb.last_seen_at = timezone.now()
    bulb.save(update_fields=["is_online", "last_seen_at"])

    return JsonResponse(
        {
            "ok": True,
            "bulb_uuid": str(bulb.uuid),
            "is_on": bulb.is_on,
            "brightness": bulb.brightness,
            "updated_at": bulb.updated_at.isoformat() if bulb.updated_at else None,
        }
    )


@csrf_exempt
@require_POST
def report_state(request):
    bulb = _authenticate_device(request)
    if bulb is None:
        return HttpResponseForbidden("Invalid or missing device token.")

    try:
        payload = json.loads(request.body.decode("utf-8"))
    except Exception:
        return HttpResponseBadRequest("Invalid JSON payload.")

    try:
        brightness = payload.get("brightness")
        if brightness is not None:
            brightness = int(brightness)

        decision = request_control_action(
            bulb=bulb,
            action=ControlActivity.ACTION_DEVICE_REPORTED,
            requested_is_on=payload.get("is_on"),
            requested_brightness=brightness,
            source_type=ControlActivity.SOURCE_DEVICE_SYNC,
            notes="Device reported state to server.",
            current_rms=payload.get("current_rms"),
            estimated_voltage=payload.get("estimated_voltage"),
            estimated_power_w=payload.get("estimated_power_w"),
            cumulative_energy_wh=payload.get("cumulative_energy_wh"),
        )
    except (TypeError, ValueError):
        return HttpResponseBadRequest("Brightness and power-reading values must be numeric.")

    return JsonResponse(
        {
            "ok": True,
            "outcome": decision.activity.outcome,
            "reason": decision.reason,
            "reason_code": decision.activity.reason_code,
            "activity_id": decision.activity.id,
            "is_on": decision.bulb.is_on,
            "brightness": decision.bulb.brightness,
            "updated_at": decision.bulb.updated_at.isoformat() if decision.bulb.updated_at else None,
        }
    )
