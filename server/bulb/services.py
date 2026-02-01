from django.utils import timezone
from .models import LightState, ControlActivity
from .device import DeviceClient

device = DeviceClient()

def get_state() -> LightState:
    # Keep a single row by always using id=1
    state, _ = LightState.objects.get_or_create(id=1, defaults={"is_on": False})
    return state

def set_light(on: bool) -> LightState:
    # Call device first (mock). Later you can handle exceptions here.
    device.set_power(on)

    state = get_state()
    state.is_on = on
    state.updated_at = timezone.now()
    state.save(update_fields=["is_on", "updated_at"])

    ControlActivity.objects.create(action="ON" if on else "OFF")
    return state

def set_brightness(brightness: int) -> LightState:
    
    brightness = max(0, min(100, int(brightness)))

    device.set_brightness(brightness)

    state = get_state()
    state.brightness = brightness
    state.updated_at = timezone.now()
    state.save(update_fields=["brightness", "updated_at"])

    ControlActivity.objects.create(action="BRIGHTNESS", value=str(brightness))
    return state
