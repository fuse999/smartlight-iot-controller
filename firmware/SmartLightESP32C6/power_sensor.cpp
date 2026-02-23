#include "power_sensor.h"
#include "pins.h"

/*
  Calibration placeholders:
  - offsetVolts: sensor output at 0A (often ~Vcc/2)
  - voltsPerAmp: depends on ACS712 variant (e.g., 185mV/A, 100mV/A, 66mV/A)
  You will measure/tune these once hardware is assembled.
*/
static float offsetVolts = 1.65f;     // typical mid-point for 3.3V system
static float voltsPerAmp = 0.185f;    // EXAMPLE ONLY (depends on your sensor!)

static float adcToVolts(int adc) {
  // ESP32 ADC range varies; Arduino core often maps to 0..4095.
  // Use 3.3V as reference for now (adjust if your board uses different attenuation).
  return (adc / 4095.0f) * 3.3f;
}

void power_sensor_init() {
  pinMode(PIN_POWER_SENSE, INPUT);
}

float power_sensor_read_rms_amps(uint16_t sampleCount, uint32_t sampleDelayUs) {
  // RMS of (signal - offset)
  double sumSq = 0.0;

  for (uint16_t i = 0; i < sampleCount; i++) {
    int raw = analogRead(PIN_POWER_SENSE);
    float v = adcToVolts(raw);
    float vCentered = v - offsetVolts;

    sumSq += (double)vCentered * (double)vCentered;

    if (sampleDelayUs > 0) delayMicroseconds(sampleDelayUs);
  }

  float vrms = sqrt(sumSq / sampleCount);
  float irms = vrms / voltsPerAmp;
  return irms;
}
