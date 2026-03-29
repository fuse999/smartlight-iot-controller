#include <Arduino.h>

#include "config.h"
#include "types.h"
#include "wifi_mgr.h"
#include "api_client.h"
#include "light_controller.h"

static uint32_t lastPoll = 0;

void setup() {
  Serial.begin(115200);
  delay(200);

  light_controller_init();
  wifi_connect_blocking();

  Serial.println("Registering device with server...");
  if (api_register_device()) {
    Serial.println("Device registration successful.");
  } else {
    Serial.println("Device registration failed.");
  }
}

void loop() {
  wifi_ensure_connected();

  const uint32_t now = millis();

  if (now - lastPoll >= POLL_INTERVAL_MS) {
    lastPoll = now;

    LightState desired;

    if (api_fetch_desired(desired)) {
      LightState applied = light_controller_get_applied();

      if (!statesEqual(desired, applied)) {
        Serial.print("Applying: on=");
        Serial.print(desired.is_on);
        Serial.print(" brightness=");
        Serial.println(desired.brightness);

        light_controller_apply(desired);
        api_report_applied(desired);
      }
    }
  }

  delay(10);
}