/*
  ============================================================
  LIGHT CONTROLLER IMPLEMENTATION
  ============================================================

  This module is responsible for controlling the physical
  output hardware of the device.

  CURRENT IMPLEMENTATION (Prototype / Demo):
    - Built-in LED shows ON/OFF state (PIN_POWER_LED)
    - External LED on PIN_DIM_OUT shows brightness using PWM

  FUTURE IMPLEMENTATION (Final Prototype):
    - PIN_DIM_OUT will drive a TRIAC gate (via optotriac)
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
//
// We store the currently applied state here so that the main
// loop can compare desired vs applied.
//
// This helps us avoid:
//   - Reapplying the same state repeatedly
//   - Spamming the server with duplicate reports
//
static LightState applied;


// ============================================================
// PWM Configuration (Demo Only)
// ============================================================
//
// We use ESP32's LEDC hardware PWM system.
//
static const int PWM_FREQ = 5000;   // 5000 Hz: fast enough to avoid visible flicker
static const int PWM_RES_BITS = 8;  // 8-bit resolution => duty cycle 0..255


// ============================================================
// clampBrightness()
// ============================================================
//
// Ensures brightness stays within valid bounds (0–100).
//
// This protects against invalid server data or accidental
// out-of-range values.
//
static int clampBrightness(int b) {
  if (b < 0) return 0;
  if (b > 100) return 100;
  return b;
}


// ============================================================
// percentToDuty()
// ============================================================
//
// Converts brightness from percent (0–100) into an 8-bit PWM
// duty cycle (0–255).
//
// If brightness = 0%, duty = 0
// If brightness = 100%, duty = 255
//
static uint8_t percentToDuty(int brightnessPercent) {
  brightnessPercent = clampBrightness(brightnessPercent);
  return (uint8_t)((brightnessPercent * 255) / 100);
}


// ============================================================
// light_controller_init()
// ============================================================
//
// Called once during setup().
//
// Responsibilities:
//  - Configure output pins
//  - Initialize PWM system for brightness demo
//  - Set initial light state to OFF
//
void light_controller_init() {

  // ----------------------------------------------------------
  // Configure ON/OFF indicator LED
  // ----------------------------------------------------------
  pinMode(PIN_POWER_LED, OUTPUT);

  // Start "OFF" respecting polarity
  digitalWrite(PIN_POWER_LED, PIN_POWER_LED_ACTIVE_LOW ? HIGH : LOW);

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
  // Initialize applied state structure
  // ----------------------------------------------------------
  applied = LightState{};
}


// ============================================================
// light_controller_apply()
// ============================================================
//
// Applies a new LightState to physical hardware.
//
// Steps:
//  1. Copy requested state (so we can safely sanitize it)
//  2. Clamp brightness to 0–100
//  3. Update ON/OFF indicator LED
//  4. Convert brightness to PWM duty (0–255)
//  5. Write PWM output (external LED demo)
//  6. Store applied state internally
//
void light_controller_apply(const LightState& s) {

  // Make a local copy so we can safely modify / sanitize it
  LightState next = s;

  // Ensure brightness is valid
  next.brightness = clampBrightness(next.brightness);

  // ----------------------------------------------------------
  // ON/OFF Indicator LED
  // ----------------------------------------------------------
  // Determine correct electrical level based on LED polarity
  const uint8_t ledOnLevel  = PIN_POWER_LED_ACTIVE_LOW ? LOW  : HIGH;
  const uint8_t ledOffLevel = PIN_POWER_LED_ACTIVE_LOW ? HIGH : LOW;

  digitalWrite(PIN_POWER_LED, next.is_on ? ledOnLevel : ledOffLevel);

  // ----------------------------------------------------------
  // Brightness Control (PWM Demo)
  // ----------------------------------------------------------
  //
  // If the light is OFF, we force duty to 0.
  // If the light is ON, we compute duty from brightness.
  //
  uint8_t duty = 0;

  if (next.is_on) {
    duty = percentToDuty(next.brightness);
  }

  // Invert duty for active-low LED (so 0 = OFF, 255 = FULL ON logically)
  if (PIN_DIM_ACTIVE_LOW) {
    duty = 255 - duty;
  }

  // Core 3.x API (ESP32-C6)
  ledcWrite(PIN_DIM_OUT, duty);

  // ----------------------------------------------------------
  // Store the newly applied state
  // ----------------------------------------------------------
  applied = next;
}


// ============================================================
// light_controller_get_applied()
// ============================================================
//
// Returns the most recently applied light state.
//
// Used by the main loop to compare against the desired state
// from the server.
//
// This prevents unnecessary reapplication and server spam.
//
LightState light_controller_get_applied() {
  return applied;
}
