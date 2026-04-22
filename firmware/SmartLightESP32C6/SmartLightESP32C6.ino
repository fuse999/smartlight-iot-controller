#include <Arduino.h>
#include <math.h>

#include "config.h"
#include "types.h"
#include "wifi_mgr.h"
#include "api_client.h"
#include "light_controller.h"
#include "power_sensor.h"

static uint32_t lastPoll = 0;
static uint32_t lastPowerSample = 0;
static uint32_t lastPowerReport = 0;
static uint32_t lastEnergyUpdate = 0;

static float lastMeasuredAmps = 0.0f;
static float smoothedPowerW = 0.0f;
static float cumulativeEnergyWh = 0.0f;
static bool powerInitialized = false;

static void updatePowerMetrics(uint32_t now) {
  LightState applied = light_controller_get_applied();

  // Integrate energy using the previous smoothed power value over the elapsed time.
  if (lastEnergyUpdate != 0) {
    const uint32_t dtMs = now - lastEnergyUpdate;
    cumulativeEnergyWh += (smoothedPowerW * (float)dtMs) / 3600000.0f;
  }
  lastEnergyUpdate = now;

  float amps = 0.0f;
  float rawPowerW = 0.0f;

  if (applied.is_on) {
    amps = power_sensor_read_rms_amps(500, 200);
    rawPowerW = amps * ESTIMATED_LINE_VOLTAGE;
  } else {
    // If the light is off, force power to zero immediately.
    amps = 0.0f;
    rawPowerW = 0.0f;
  }

  lastMeasuredAmps = amps;

  if (!powerInitialized) {
    smoothedPowerW = rawPowerW;
    powerInitialized = true;
  } else if (applied.is_on) {
    smoothedPowerW =
      (POWER_SMOOTHING_ALPHA * rawPowerW) +
      ((1.0f - POWER_SMOOTHING_ALPHA) * smoothedPowerW);
  } else {
    smoothedPowerW = 0.0f;
  }

  Serial.print("Irms: ");
  Serial.print(lastMeasuredAmps, 3);
  Serial.print(" A | Raw Power: ");
  Serial.print(rawPowerW, 1);
  Serial.print(" W | Smoothed Power: ");
  Serial.print(smoothedPowerW, 1);
  Serial.print(" W | Reported Power: ");
  Serial.print((int)roundf(smoothedPowerW));
  Serial.print(" W | Energy: ");
  Serial.print(cumulativeEnergyWh, 3);
  Serial.println(" Wh");
}

static bool reportCurrentStateToServer() {
  LightState applied = light_controller_get_applied();

  float reportedPowerW = applied.is_on ? (float)((int)roundf(smoothedPowerW)) : 0.0f;
  float reportedCurrentRms = applied.is_on ? lastMeasuredAmps : 0.0f;
  float reportedVoltage = applied.is_on ? ESTIMATED_LINE_VOLTAGE : 0.0f;

  Serial.print("Reporting state: on=");
  Serial.print(applied.is_on);
  Serial.print(" brightness=");
  Serial.print(applied.brightness);
  Serial.print(" current_rms=");
  Serial.print(reportedCurrentRms, 3);
  Serial.print(" estimated_power_w=");
  Serial.print(reportedPowerW, 1);
  Serial.print(" cumulative_energy_wh=");
  Serial.println(cumulativeEnergyWh, 3);

  return api_report_applied(
    applied,
    reportedCurrentRms,
    reportedVoltage,
    reportedPowerW,
    cumulativeEnergyWh
  );
}

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

  const uint32_t now = millis();

  // Initialize power/energy timers and send an initial report.
  lastPowerSample = now;
  lastPowerReport = now;
  lastEnergyUpdate = now;

  updatePowerMetrics(now);
  reportCurrentStateToServer();
}

void loop() {
  wifi_ensure_connected();

  const uint32_t now = millis();

  // Sample power on a regular interval.
  if (now - lastPowerSample >= POWER_SAMPLE_INTERVAL_MS) {
    lastPowerSample = now;
    updatePowerMetrics(now);
  }

  // Poll server for desired state changes.
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

        // Refresh power metrics immediately after a state change.
        updatePowerMetrics(now);

        if (reportCurrentStateToServer()) {
          lastPowerReport = now;
        }
      }
    }
  }

  // Periodic reporting:
  // - every 10 seconds while ON
  // - every 60 seconds while OFF
  {
    LightState applied = light_controller_get_applied();
    const uint32_t reportInterval =
      applied.is_on ? POWER_REPORT_ON_INTERVAL_MS : POWER_REPORT_OFF_INTERVAL_MS;

    if (now - lastPowerReport >= reportInterval) {
      if (reportCurrentStateToServer()) {
        lastPowerReport = now;
      }
    }
  }

  delay(10);
}