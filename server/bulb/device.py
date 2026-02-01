import logging

logger = logging.getLogger(__name__)

class DeviceClient:
    """
    Mock device client for now.
    Replace implementation later with real ESP32 control.
    """
    def set_power(self, on: bool) -> None:
        logger.info("MOCK DEVICE: set_power(%s)", "ON" if on else "OFF")