#pragma once

/*
  ============================================================
  HARDWARE PIN DEFINITIONS
  ============================================================

  This file defines ALL hardware pin assignments.

  Why isolate pin definitions?
  --------------------------------
  If we change wiring in the future (for example when we
  replace LEDs with a TRIAC dimmer), we only update this file.

  This avoids "magic numbers" inside logic code.
*/



// ============================================================
// Power Indicator LED
//
// Shows whether the light is ON or OFF.
// ============================================================

static const int PIN_POWER_LED = 15;
// TRUE = LED turns on when pin is LOW
// FALSE = LED turns on when pin is HIGH
static const bool PIN_POWER_LED_ACTIVE_LOW = true;


// ============================================================
// Brightness Control Output
//
// Currently:
//   Drives onboard LED using PWM for demonstration.
//
// Future:
//   Will drive TRIAC gate for AC dimming.
//
// ============================================================
static const int PIN_DIM_OUT = 15;
static const bool PIN_DIM_ACTIVE_LOW = true;


// ============================================================
// Reserved for Future Hardware
//
// static const int PIN_ZC_IN = 5;
// ============================================================
