/*
  ============================================================
  SMART LIGHT MAIN PROGRAM
  ============================================================

  Firmware Execution Flow:

    1. Initialize hardware
    2. Connect to WiFi
    3. Enter loop:
         - Ensure WiFi connection
         - Poll server for desired state
         - If state changed:
              • Apply state to hardware
              • Report applied state back to server

  The main file intentionally contains minimal logic.
  All heavy lifting is done in modules.
*/

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

  light_controller_init();    // Initialize LEDs / PWM
  wifi_connect_blocking();    // Connect to WiFi
}

void loop() {
  wifi_ensure_connected();    // Maintain connection

  const uint32_t now = millis();

  // Poll server at defined interval
  if (now - lastPoll >= POLL_INTERVAL_MS) {
    lastPoll = now;

    LightState desired;

    if (api_fetch_desired(desired)) {

      LightState applied = light_controller_get_applied();

      // Only apply if state changed
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

  delay(10);  // Small delay to reduce CPU load
}
