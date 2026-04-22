#pragma once
#include <Arduino.h>
#include "types.h"

String makeUrl(const char* path);

bool api_register_device();
bool api_fetch_desired(LightState& out);

bool api_report_applied(
  const LightState& s,
  float currentRms,
  float estimatedVoltage,
  float estimatedPowerW,
  float cumulativeEnergyWh
);