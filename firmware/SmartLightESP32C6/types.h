#pragma once

/*
  This file defines shared data structures used across the project.

  Instead of passing separate variables like:
      bool is_on;
      int brightness;

  We bundle them together into a single structure called LightState.
  This keeps the firmware clean and scalable.
*/

struct LightState {
  bool is_on = false;      // True = light ON, False = light OFF
  int brightness = 100;    // Brightness percentage (0–100)
};

/*
  Helper function that compares two LightState objects.

  Returns true if both ON/OFF and brightness are identical.

  We use this to avoid re-applying the same state repeatedly
  and to prevent unnecessary server reports.
*/
inline bool statesEqual(const LightState& a, const LightState& b) {
  return a.is_on == b.is_on && a.brightness == b.brightness;
}
