#pragma once
#include <Arduino.h>

/*
  ============================================================
  POWER SENSOR MODULE
  ============================================================

  Reads an analog current sensor on an ADC pin (GPIO 0).

  Notes:
  - For AC current sensing, we sample many points and compute RMS.
  - Calibration is required (sensor offset + scale factor).
*/

void power_sensor_init();
float power_sensor_read_rms_amps(uint16_t sampleCount, uint32_t sampleDelayUs);
