/*
  ============================================================
  LIGHT CONTROLLER IMPLEMENTATION
  ============================================================

  This module is responsible for controlling the physical
  output hardware of the device.

  CURRENT IMPLEMENTATION (Prototype / Demo):
    - Built-in LED shows ON/OFF state (PIN_POWER_LED)
    - External LED on PIN_DIM_OUT shows brightness using PWM

  UPDATED IMPLEMENTATION (Hardware Bring-Up):
    - Relay output added (PIN_RELAY_OUT) for hard ON/OFF control
    - Optotriac drive output added (PIN_MOC_OUT) to trigger MOC3023 input side
    - PWM LED demo is kept so we can validate brightness logic without
      powering the AC dimmer stage yet

  FUTURE IMPLEMENTATION (Final Prototype):
    - PIN_DIM_OUT may drive TRIAC gate timing logic (phase-angle dimming)
    - Zero-cross detection will be added (PIN_ZC_IN)
    - PWM will likely be replaced with phase-angle timing logic
      (because AC dimming is not standard PWM)

  IMPORTANT DESIGN PRINCIPLE:
  ------------------------------------------------------------
  The rest of the firmware DOES NOT know how the light works.
  It only calls:

      light_controller_apply(state);

  That means we can swap out the internals of this module
  later (LED demo -> TRIAC phase control) without changing
  API, WiFi, or main loop logic.
*/

#include "light_controller.h"
#include "pins.h"
#include <Arduino.h>


// ============================================================
// Internal state storage
// ============================================================
static LightState applied;


// ============================================================
// PWM Configuration (Demo Only)
// ============================================================
static const int PWM_FREQ = 5000;   // 5000 Hz: fast enough to avoid visible flicker
static const int PWM_RES_BITS = 8;  // 8-bit resolution => duty cycle 0..255


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
static uint8_t percentToDuty(int brightnessPercent) {
  brightnessPercent = clampBrightness(brightnessPercent);
  return (uint8_t)((brightnessPercent * 255) / 100);
}


// ============================================================
// light_controller_init()
// ============================================================
void light_controller_init() {

  // ----------------------------------------------------------
  // Configure ON/OFF indicator LED
  // ----------------------------------------------------------
  pinMode(PIN_POWER_LED, OUTPUT);

  // Start "OFF" respecting polarity
  digitalWrite(PIN_POWER_LED, PIN_POWER_LED_ACTIVE_LOW ? HIGH : LOW);

  // ----------------------------------------------------------
  // Configure Relay Output
  // ----------------------------------------------------------
  pinMode(PIN_RELAY_OUT, OUTPUT);

  // Start OFF respecting polarity
  digitalWrite(PIN_RELAY_OUT, PIN_RELAY_ACTIVE_LOW ? HIGH : LOW);

  // ----------------------------------------------------------
  // Configure PWM output for brightness demonstration
  // ----------------------------------------------------------
  //
  // ESP32-C6 core 3.x:
  //   ledcAttach(pin, freq, resolutionBits)
  //   ledcWrite(pin, duty)
  //
  ledcAttach(PIN_DIM_OUT, PWM_FREQ, PWM_RES_BITS);

  // Start OFF (duty=0 logical), then invert if active-low
  uint8_t startDuty = 0;
  if (PIN_DIM_ACTIVE_LOW) {
    startDuty = 255 - startDuty;
  }
  ledcWrite(PIN_DIM_OUT, startDuty);

  // ----------------------------------------------------------
  // Configure MOC3023 drive output (optotriac input side)
  // ----------------------------------------------------------
  //
  // This pin is intended to drive the *LED input* of the optotriac
  // stage (usually through a resistor and/or transistor like PN2222A).
  //
  // NOTE:
  //   Depending on how your transistor stage is wired, "active LOW"
  //   may be required. We handle that with PIN_MOC_ACTIVE_LOW.
  //
  pinMode(PIN_MOC_OUT, OUTPUT);

  const uint8_t mocOffLevel = PIN_MOC_ACTIVE_LOW ? HIGH : LOW;
  digitalWrite(PIN_MOC_OUT, mocOffLevel);

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

  // Ensure brightness is valid
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

  digitalWrite(PIN_RELAY_OUT, next.is_on ? relayOnLevel : relayOffLevel);

  // Optional debug
  Serial.println(next.is_on ? "Relay ON" : "Relay OFF");

  // ----------------------------------------------------------
  // MOC3023 Drive (Optotriac input side)
  // ----------------------------------------------------------
  const uint8_t mocOnLevel  = PIN_MOC_ACTIVE_LOW ? LOW  : HIGH;
  const uint8_t mocOffLevel = PIN_MOC_ACTIVE_LOW ? HIGH : LOW;

  digitalWrite(PIN_MOC_OUT, next.is_on ? mocOnLevel : mocOffLevel);

  // ----------------------------------------------------------
  // Brightness Control (PWM Demo)
  // ----------------------------------------------------------
  uint8_t duty = 0;

  if (next.is_on) {
    duty = percentToDuty(next.brightness);
  }

  // Invert duty for active-low LED (so 0 = OFF, 255 = FULL ON logically)
  if (PIN_DIM_ACTIVE_LOW) {
    duty = 255 - duty;
  }

  // Core 3.x API (ESP32-C6): write duty by PIN
  ledcWrite(PIN_DIM_OUT, duty);

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