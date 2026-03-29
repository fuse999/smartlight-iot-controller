from datetime import timedelta
import json

from django.http import JsonResponse, HttpResponseBadRequest, HttpResponseForbidden
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from .models import Bulb, ControlActivity, PowerReading


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
        ControlActivity.objects.create(
            bulb=bulb,
            user=None,
            action=ControlActivity.ACTION_DEVICE_REPORTED,
            value=device_id or "registered",
            notes="Device auto-registered with server.",
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

    changed_fields = []

    if "is_on" in payload:
        bulb.is_on = bool(payload["is_on"])
        changed_fields.append("is_on")

    if "brightness" in payload:
        try:
            bulb.brightness = max(0, min(100, int(payload["brightness"])))
            changed_fields.append("brightness")
        except (TypeError, ValueError):
            return HttpResponseBadRequest("Brightness must be an integer from 0 to 100.")

    bulb.is_online = True
    bulb.last_seen_at = timezone.now()
    bulb.updated_at = timezone.now()
    changed_fields.extend(["is_online", "last_seen_at", "updated_at"])

    if changed_fields:
        bulb.save(update_fields=list(dict.fromkeys(changed_fields)))

    ControlActivity.objects.create(
        bulb=bulb,
        user=None,
        action=ControlActivity.ACTION_DEVICE_REPORTED,
        value=json.dumps(
            {
                "is_on": bulb.is_on,
                "brightness": bulb.brightness,
            }
        ),
        notes="Device reported state to server.",
    )

    current_rms = payload.get("current_rms")
    estimated_voltage = payload.get("estimated_voltage")
    estimated_power_w = payload.get("estimated_power_w")
    cumulative_energy_wh = payload.get("cumulative_energy_wh")

    if current_rms is not None or estimated_power_w is not None:
        try:
            PowerReading.objects.create(
                bulb=bulb,
                current_rms=float(current_rms or 0.0),
                estimated_voltage=float(estimated_voltage or 120.0),
                estimated_power_w=float(estimated_power_w or 0.0),
                cumulative_energy_wh=float(cumulative_energy_wh or 0.0),
            )
        except (TypeError, ValueError):
            return HttpResponseBadRequest("Power-reading values must be numeric.")

    return JsonResponse({"ok": True})