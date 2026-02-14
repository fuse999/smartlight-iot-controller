#pragma once
#include "types.h"

/*
  ============================================================
  LIGHT CONTROLLER HEADER
  ============================================================

  This module controls the physical hardware.

  Currently:
    - Controls onboard LED

  Future:
    - Will control TRIAC dimmer
    - Will integrate zero-cross detection
*/

// Initialize hardware
void light_controller_init();

// Apply a LightState to hardware
void light_controller_apply(const LightState& s);

// Retrieve currently applied state
LightState light_controller_get_applied();
