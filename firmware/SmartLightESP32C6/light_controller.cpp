#include "light_controller.h"
#include "pins.h"
#include <Arduino.h>
#include <esp_timer.h>

// ============================================================
// Internal state storage
// ============================================================
static LightState applied;

// ============================================================
// AC Dimming Configuration
// ============================================================
//
// 60 Hz mains:
//   Full cycle  = 16.67 ms
//   Half cycle  =  8.33 ms = 8333 us
//
static const uint32_t AC_HALF_CYCLE_US   = 8333;
static const uint32_t TRIAC_PULSE_US     = 800;

// Use the working range you found during testing
static const uint32_t TRIAC_MIN_DELAY_US = 100;
static const uint32_t TRIAC_MAX_DELAY_US = 2500;

// ============================================================
// Timer Handles for Phase Control
// ============================================================
static esp_timer_handle_t triacOnTimer  = nullptr;
static esp_timer_handle_t triacOffTimer = nullptr;

// ============================================================
// Volatile phase-control state
// ============================================================
static volatile bool phaseControlEnabled = false;
static volatile uint32_t nextFireDelayUs = TRIAC_MAX_DELAY_US;
static volatile uint32_t lastAcceptedZcUs = 0;

// ============================================================
// Helper: output levels
// ============================================================
static inline uint8_t mocOnLevel() {
  return PIN_MOC_ACTIVE_LOW ? LOW : HIGH;
}

static inline uint8_t mocOffLevel() {
  return PIN_MOC_ACTIVE_LOW ? HIGH : LOW;
}

// ============================================================
// clampBrightness()
// ============================================================
static int clampBrightness(int b) {
  if (b < 0) return 0;
  if (b > 100) return 100;
  return b;
}

// ============================================================
// brightnessToDelayUs()
// ============================================================
// 100% brightness => shortest delay
// 0% brightness   => longest delay in your known-good range
// ============================================================
static uint32_t brightnessToDelayUs(int brightnessPercent) {
  brightnessPercent = clampBrightness(brightnessPercent);

  if (brightnessPercent <= 0) return TRIAC_MAX_DELAY_US;
  if (brightnessPercent >= 100) return TRIAC_MIN_DELAY_US;

  const uint32_t span = TRIAC_MAX_DELAY_US - TRIAC_MIN_DELAY_US;

  uint32_t delayUs = TRIAC_MAX_DELAY_US
                   - ((uint32_t)brightnessPercent * span) / 100;

  if (delayUs < TRIAC_MIN_DELAY_US) delayUs = TRIAC_MIN_DELAY_US;
  if (delayUs > TRIAC_MAX_DELAY_US) delayUs = TRIAC_MAX_DELAY_US;

  return delayUs;
}

// ============================================================
// triacPulseOffCallback()
// ============================================================
static void triacPulseOffCallback(void* arg) {
  (void)arg;
  digitalWrite(PIN_MOC_OUT, mocOffLevel());
}

// ============================================================
// triacPulseOnCallback()
// ============================================================
static void triacPulseOnCallback(void* arg) {
  (void)arg;

  if (!phaseControlEnabled) {
    digitalWrite(PIN_MOC_OUT, mocOffLevel());
    return;
  }

  digitalWrite(PIN_MOC_OUT, mocOnLevel());

  if (triacOffTimer != nullptr) {
    esp_timer_stop(triacOffTimer);
    esp_timer_start_once(triacOffTimer, TRIAC_PULSE_US);
  }
}

// ============================================================
// zeroCrossISR()
// ============================================================
static void IRAM_ATTR zeroCrossISR() {
  uint32_t now = micros();

  // Ignore noise / duplicate edges near the same crossing
  if (now - lastAcceptedZcUs < 2000) {
    return;
  }
  lastAcceptedZcUs = now;

  if (!phaseControlEnabled) {
    return;
  }

  if (triacOnTimer != nullptr) {
    esp_timer_stop(triacOnTimer);
    esp_timer_start_once(triacOnTimer, nextFireDelayUs);
  }
}

// ============================================================
// stopPhaseControlOutput()
// ============================================================
static void stopPhaseControlOutput() {
  phaseControlEnabled = false;

  if (triacOnTimer != nullptr) {
    esp_timer_stop(triacOnTimer);
  }

  if (triacOffTimer != nullptr) {
    esp_timer_stop(triacOffTimer);
  }

  digitalWrite(PIN_MOC_OUT, mocOffLevel());
}

// ============================================================
// updatePhaseControlSettings()
// ============================================================
static void updatePhaseControlSettings(const LightState& s) {
  if (!s.is_on || s.brightness <= 0) {
    stopPhaseControlOutput();
    Serial.println("Phase control OFF");
    return;
  }

  nextFireDelayUs = brightnessToDelayUs(s.brightness);
  phaseControlEnabled = true;

  Serial.printf("Phase control ON | brightness=%d%% | fireDelay=%lu us\n",
                s.brightness,
                (unsigned long)nextFireDelayUs);
}

// ============================================================
// initPhaseControl()
// ============================================================
static void initPhaseControl() {
  pinMode(PIN_ZC_IN, INPUT_PULLUP);   // use INPUT if you already have external pull-up

  esp_timer_create_args_t onArgs = {};
  onArgs.callback = &triacPulseOnCallback;
  onArgs.arg = nullptr;
  onArgs.dispatch_method = ESP_TIMER_TASK;
  onArgs.name = "triac_on";

  esp_timer_create_args_t offArgs = {};
  offArgs.callback = &triacPulseOffCallback;
  offArgs.arg = nullptr;
  offArgs.dispatch_method = ESP_TIMER_TASK;
  offArgs.name = "triac_off";

  esp_timer_create(&onArgs, &triacOnTimer);
  esp_timer_create(&offArgs, &triacOffTimer);

  attachInterrupt(digitalPinToInterrupt(PIN_ZC_IN), zeroCrossISR, FALLING);
}

// ============================================================
// light_controller_init()
// ============================================================
void light_controller_init() {
  pinMode(PIN_POWER_LED, OUTPUT);
  digitalWrite(PIN_POWER_LED, PIN_POWER_LED_ACTIVE_LOW ? HIGH : LOW);

  pinMode(PIN_RELAY_OUT, OUTPUT);
  digitalWrite(PIN_RELAY_OUT, PIN_RELAY_ACTIVE_LOW ? HIGH : LOW);

  pinMode(PIN_MOC_OUT, OUTPUT);
  digitalWrite(PIN_MOC_OUT, mocOffLevel());

  initPhaseControl();

  applied = LightState{};
}

// ============================================================
// light_controller_apply()
// ============================================================
void light_controller_apply(const LightState& s) {
  LightState next = s;
  next.brightness = clampBrightness(next.brightness);

  const uint8_t ledOnLevel  = PIN_POWER_LED_ACTIVE_LOW ? LOW  : HIGH;
  const uint8_t ledOffLevel = PIN_POWER_LED_ACTIVE_LOW ? HIGH : LOW;
  digitalWrite(PIN_POWER_LED, next.is_on ? ledOnLevel : ledOffLevel);

  const uint8_t relayOnLevel  = PIN_RELAY_ACTIVE_LOW ? LOW  : HIGH;
  const uint8_t relayOffLevel = PIN_RELAY_ACTIVE_LOW ? HIGH : LOW;

  const bool outputEnabled = next.is_on && (next.brightness > 0);
  digitalWrite(PIN_RELAY_OUT, outputEnabled ? relayOnLevel : relayOffLevel);

  Serial.printf("Relay %s | Brightness=%d%%\n",
                outputEnabled ? "ON" : "OFF",
                next.brightness);

  updatePhaseControlSettings(next);

  applied = next;
}

// ============================================================
// light_controller_get_applied()
// ============================================================
LightState light_controller_get_applied() {
  return applied;
}