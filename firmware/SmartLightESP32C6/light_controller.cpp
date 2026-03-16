/*
  ============================================================
  LIGHT CONTROLLER IMPLEMENTATION
  ============================================================

  This module is responsible for controlling the physical
  output hardware of the device.

  UPDATED IMPLEMENTATION:
    - Built-in LED shows ON/OFF state (PIN_POWER_LED)
    - Relay output added (PIN_RELAY_OUT) for hard ON/OFF control
    - MOC3023 output uses phase-angle dimming with zero-cross sync
    - PWM LED demo on PIN_DIM_OUT is kept as fallback / debug path

  IMPORTANT:
    MOC3023 dimming is NOT PWM. It uses:
      1. zero-cross detection
      2. delay into each half-cycle
      3. short optotriac trigger pulse

  DESIGN NOTES:
    - Brightness 100% = fire almost immediately after zero-cross
    - Brightness 0%   = do not fire
    - Relay remains the "master power enable"
    - TRIAC firing repeats every half-cycle while light is ON
*/

#include "light_controller.h"
#include "pins.h"
#include <Arduino.h>
#include <esp_timer.h>


// ============================================================
// Internal state storage
// ============================================================
static LightState applied;


// ============================================================
// PWM Configuration (Demo / Fallback Only)
// ============================================================
static const int PWM_FREQ = 5000;
static const int PWM_RES_BITS = 8;


// ============================================================
// AC Dimming Configuration
// ============================================================
//
// 60 Hz mains:
//   Full cycle  = 16.67 ms
//   Half cycle  =  8.33 ms = 8333 us
//
// We leave a little margin at the end of the half-cycle so
// the pulse does not run into the next zero crossing.
//
static const uint32_t AC_HALF_CYCLE_US   = 8333;
static const uint32_t TRIAC_PULSE_US     = 150;   // MOC3023 pulse width
static const uint32_t TRIAC_MIN_DELAY_US = 200;   // brightest practical firing delay
static const uint32_t TRIAC_MAX_DELAY_US = 8000;  // dimmest practical firing delay


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
// percentToDuty()
// ============================================================
// Used only for PWM demo / fallback path
// ============================================================
static uint8_t percentToDuty(int brightnessPercent) {
  brightnessPercent = clampBrightness(brightnessPercent);
  return (uint8_t)((brightnessPercent * 255) / 100);
}


// ============================================================
// brightnessToDelayUs()
// ============================================================
//
// Convert brightness percent to phase delay.
//
// brighter => shorter delay
// dimmer   => longer delay
//
// 100% -> TRIAC_MIN_DELAY_US
//   1% -> near TRIAC_MAX_DELAY_US
//   0% -> special case handled outside (do not fire)
// ============================================================
static uint32_t brightnessToDelayUs(int brightnessPercent) {
  brightnessPercent = clampBrightness(brightnessPercent);

  if (brightnessPercent <= 0) {
    return TRIAC_MAX_DELAY_US;
  }

  if (brightnessPercent >= 100) {
    return TRIAC_MIN_DELAY_US;
  }

  const uint32_t span = TRIAC_MAX_DELAY_US - TRIAC_MIN_DELAY_US;

  // Invert brightness into delay
  // 100% => 0 span added
  // 1%   => almost full span added
  uint32_t delayUs = TRIAC_MIN_DELAY_US +
                     ((uint32_t)(100 - brightnessPercent) * span) / 100;

  if (delayUs < TRIAC_MIN_DELAY_US) delayUs = TRIAC_MIN_DELAY_US;
  if (delayUs > TRIAC_MAX_DELAY_US) delayUs = TRIAC_MAX_DELAY_US;

  return delayUs;
}


// ============================================================
// triacPulseOffCallback()
// ============================================================
// Ends the short MOC3023 trigger pulse
// ============================================================
static void triacPulseOffCallback(void* arg) {
  (void)arg;
  digitalWrite(PIN_MOC_OUT, mocOffLevel());
}


// ============================================================
// triacPulseOnCallback()
// ============================================================
// Starts the MOC3023 pulse, then schedules pulse-off
// ============================================================
static void triacPulseOnCallback(void* arg) {
  (void)arg;

  if (!phaseControlEnabled) {
    digitalWrite(PIN_MOC_OUT, mocOffLevel());
    return;
  }

  digitalWrite(PIN_MOC_OUT, mocOnLevel());

  // schedule end of pulse
  if (triacOffTimer != nullptr) {
    esp_timer_stop(triacOffTimer);
    esp_timer_start_once(triacOffTimer, TRIAC_PULSE_US);
  }
}


// ============================================================
// zeroCrossISR()
// ============================================================
//
// Called every AC half-cycle if zero-cross hardware is present.
// We schedule a delayed pulse for the MOC3023.
//
// Keep ISR short.
// ============================================================
static void IRAM_ATTR zeroCrossISR() {
  if (!phaseControlEnabled) {
    return;
  }

  if (triacOnTimer != nullptr) {
    esp_timer_stop(triacOnTimer);
    esp_timer_start_once(triacOnTimer, nextFireDelayUs);
  }
}


// ============================================================
// initPhaseControl()
// ============================================================
static void initPhaseControl() {
#if defined(PIN_ZC_IN)
  pinMode(PIN_ZC_IN, INPUT);

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

  attachInterrupt(digitalPinToInterrupt(PIN_ZC_IN), zeroCrossISR, RISING);
#endif
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
    return;
  }

  nextFireDelayUs = brightnessToDelayUs(s.brightness);
  phaseControlEnabled = true;
}


// ============================================================
// writePwmDemo()
// ============================================================
// Optional LED demo / fallback path
// ============================================================
static void writePwmDemo(const LightState& s) {
  uint8_t duty = 0;

  if (s.is_on) {
    duty = percentToDuty(s.brightness);
  }

  if (PIN_DIM_ACTIVE_LOW) {
    duty = 255 - duty;
  }

  ledcWrite(PIN_DIM_OUT, duty);
}


// ============================================================
// light_controller_init()
// ============================================================
void light_controller_init() {

  // ----------------------------------------------------------
  // Configure ON/OFF indicator LED
  // ----------------------------------------------------------
  pinMode(PIN_POWER_LED, OUTPUT);
  digitalWrite(PIN_POWER_LED, PIN_POWER_LED_ACTIVE_LOW ? HIGH : LOW);

  // ----------------------------------------------------------
  // Configure Relay Output
  // ----------------------------------------------------------
  pinMode(PIN_RELAY_OUT, OUTPUT);
  digitalWrite(PIN_RELAY_OUT, PIN_RELAY_ACTIVE_LOW ? HIGH : LOW);

  // ----------------------------------------------------------
  // Configure PWM output for debug/demo path
  // ----------------------------------------------------------
  ledcAttach(PIN_DIM_OUT, PWM_FREQ, PWM_RES_BITS);

  uint8_t startDuty = 0;
  if (PIN_DIM_ACTIVE_LOW) {
    startDuty = 255 - startDuty;
  }
  ledcWrite(PIN_DIM_OUT, startDuty);

  // ----------------------------------------------------------
  // Configure MOC3023 drive output
  // ----------------------------------------------------------
  pinMode(PIN_MOC_OUT, OUTPUT);
  digitalWrite(PIN_MOC_OUT, mocOffLevel());

  // ----------------------------------------------------------
  // Initialize phase-control hardware
  // ----------------------------------------------------------
  initPhaseControl();

  // ----------------------------------------------------------
  // Initialize applied state structure
  // ----------------------------------------------------------
  applied = LightState{};
}


// ============================================================
// light_controller_apply()
// ============================================================
void light_controller_apply(const LightState& s) {

  // Make a local copy so we can safely modify / sanitize it
  LightState next = s;
  next.brightness = clampBrightness(next.brightness);

  // ----------------------------------------------------------
  // ON/OFF Indicator LED
  // ----------------------------------------------------------
  const uint8_t ledOnLevel  = PIN_POWER_LED_ACTIVE_LOW ? LOW  : HIGH;
  const uint8_t ledOffLevel = PIN_POWER_LED_ACTIVE_LOW ? HIGH : LOW;
  digitalWrite(PIN_POWER_LED, next.is_on ? ledOnLevel : ledOffLevel);

  // ----------------------------------------------------------
  // Relay Control (Hard ON/OFF)
  // ----------------------------------------------------------
  const uint8_t relayOnLevel  = PIN_RELAY_ACTIVE_LOW ? LOW  : HIGH;
  const uint8_t relayOffLevel = PIN_RELAY_ACTIVE_LOW ? HIGH : LOW;

  // If brightness is zero, treat as OFF for actual power output
  const bool outputEnabled = next.is_on && (next.brightness > 0);

  digitalWrite(PIN_RELAY_OUT, outputEnabled ? relayOnLevel : relayOffLevel);

  Serial.printf("Relay %s | Brightness=%d%%\n",
                outputEnabled ? "ON" : "OFF",
                next.brightness);

  // ----------------------------------------------------------
  // MOC3023 Phase-Angle Control
  // ----------------------------------------------------------
#if defined(PIN_ZC_IN)
  updatePhaseControlSettings(next);
#else
  // If no zero-cross input is defined yet, fall back to simple
  // ON/OFF drive so hardware bring-up can continue.
  digitalWrite(PIN_MOC_OUT, outputEnabled ? mocOnLevel() : mocOffLevel());
#endif

  // ----------------------------------------------------------
  // PWM LED demo / debug output
  // ----------------------------------------------------------
  writePwmDemo(next);

  // ----------------------------------------------------------
  // Store the newly applied state
  // ----------------------------------------------------------
  applied = next;
}


// ============================================================
// light_controller_get_applied()
// ============================================================
LightState light_controller_get_applied() {
  return applied;
}
