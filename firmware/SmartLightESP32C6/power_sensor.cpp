#include "power_sensor.h"
#include "pins.h"
#include <Arduino.h>
#include <math.h>

// ============================================================
// ADC / Sensor Configuration
// ============================================================

// ESP32 ADC approximation
static const float adcRef = 3.3f;
static const int adcMax = 4095;

// Temporary no-divider test
// Change to 1.5f later if you add a voltage divider
static const float dividerCorrection = 1.0f;

// ACS712 5A sensitivity
static const float voltsPerAmp = 0.185f;

// Calibrated zero-current midpoint
static float offsetVolts = 2.5f;

// Baseline RMS current when the light/load is OFF
static float baselineRmsAmps = 0.0f;

// Small deadband to suppress noise after subtraction
static const float currentDeadbandAmps = 0.01f;

// Empirical scale factor to tune readings closer to your known 5.5 W bulb
// Start here and adjust later if needed.
static const float currentScaleFactor = 1.0f;

// Number of repeated RMS measurements to average per call
static const uint8_t internalAveragePasses = 5;

// ============================================================
// Helper: ADC raw -> reconstructed sensor voltage
// ============================================================
static inline float adcRawToSensorVolts(int raw) {
  float adcVolts = (raw * adcRef) / adcMax;
  return adcVolts * dividerCorrection;
}

// ============================================================
// Calibrate ACS712 midpoint voltage
// Assumes load is OFF
// ============================================================
static void calibrateOffsetVolts() {
  const int samples = 4000;
  double sum = 0.0;

  for (int i = 0; i < samples; i++) {
    int raw = analogRead(PIN_POWER_SENSE);
    sum += adcRawToSensorVolts(raw);
    delayMicroseconds(200);
  }

  offsetVolts = (float)(sum / samples);

  Serial.print("ACS712 calibrated offsetVolts = ");
  Serial.println(offsetVolts, 5);
}

// ============================================================
// Raw RMS measurement with NO baseline subtraction
// ============================================================
static float measureRawRmsAmps(uint16_t sampleCount, uint32_t sampleDelayUs) {
  if (sampleCount == 0) return 0.0f;

  double sumSq = 0.0;

  for (uint16_t i = 0; i < sampleCount; i++) {
    int raw = analogRead(PIN_POWER_SENSE);
    float sensorVolts = adcRawToSensorVolts(raw);

    float amps = (sensorVolts - offsetVolts) / voltsPerAmp;
    sumSq += (double)amps * (double)amps;

    if (sampleDelayUs > 0) {
      delayMicroseconds((unsigned int)sampleDelayUs);
    }
  }

  return (float)sqrt(sumSq / sampleCount);
}

// ============================================================
// Calibrate baseline RMS current / idle noise floor
// Assumes light/load is OFF, but normal board electronics are running
// ============================================================
static void calibrateBaselineRms() {
  const uint16_t samples = 3000;
  const uint32_t sampleDelayUs = 200;

  double sum = 0.0;
  const uint8_t passes = 5;

  for (uint8_t i = 0; i < passes; i++) {
    sum += measureRawRmsAmps(samples, sampleDelayUs);
    delay(20);
  }

  baselineRmsAmps = (float)(sum / passes);

  Serial.print("ACS712 calibrated baselineRmsAmps = ");
  Serial.println(baselineRmsAmps, 5);
}

// ============================================================
// Public init
// IMPORTANT: Boot with the light/load OFF for best calibration
// ============================================================
void power_sensor_init() {
  pinMode(PIN_POWER_SENSE, INPUT);
  analogReadResolution(12);

  delay(250);

  calibrateOffsetVolts();
  calibrateBaselineRms();
}

// ============================================================
// Public RMS read with:
// - multiple internal averages
// - baseline subtraction
// - deadband suppression
// - empirical scale factor
// ============================================================
float power_sensor_read_rms_amps(uint16_t sampleCount, uint32_t sampleDelayUs) {
  if (sampleCount == 0) return 0.0f;

  double avgRawRms = 0.0;

  for (uint8_t pass = 0; pass < internalAveragePasses; pass++) {
    avgRawRms += measureRawRmsAmps(sampleCount, sampleDelayUs);
    delay(10);
  }

  avgRawRms /= internalAveragePasses;

  float corrected = (float)avgRawRms - baselineRmsAmps;
  if (corrected < 0.0f) corrected = 0.0f;

  if (corrected < currentDeadbandAmps) {
    corrected = 0.0f;
  }

  corrected *= currentScaleFactor;

  return corrected;
}