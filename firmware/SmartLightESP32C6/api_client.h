#pragma once
#include <Arduino.h>
#include "types.h"

/*
  ============================================================
  API CLIENT HEADER
  ============================================================

  This module handles all HTTP communication with the server.

  Responsibilities:
    - Fetch desired state from backend
    - Report applied state back to backend
*/

// Builds full URL from base + path
String makeUrl(const char* path);

// Fetch desired light state from server
bool api_fetch_desired(LightState& out);

// Report applied state back to server
bool api_report_applied(const LightState& s);
