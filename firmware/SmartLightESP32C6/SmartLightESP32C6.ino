#include <Arduino.h>

#include "config.h"
#include "types.h"
#include "wifi_mgr.h"
#include "api_client.h"
#include "light_controller.h"
#include "power_sensor.h"

static uint32_t lastPoll = 0;

void setup() {
  Serial.begin(115200);
  delay(200);

  light_controller_init();
  power_sensor_init();
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

  static uint32_t lastPowerRead = 0;
  if (now - lastPowerRead >= 5000) {
    lastPowerRead = now;

    float amps = power_sensor_read_rms_amps(500, 200);
    float apparentPower = amps * 120.0f;

    Serial.print("Irms: ");
    Serial.print(amps, 3);
    Serial.print(" A | Apparent Power: ");
    Serial.print(apparentPower, 1);
    Serial.println(" VA");
  }
}