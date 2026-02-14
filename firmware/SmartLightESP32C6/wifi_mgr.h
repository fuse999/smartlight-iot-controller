#pragma once
#include <Arduino.h>

/*
  ============================================================
  WIFI MANAGER HEADER
  ============================================================

  This module handles all WiFi connectivity logic.

  Separating WiFi code from main logic keeps the system
  modular and easier to debug.
*/

// Connects to WiFi and blocks until connected
void wifi_connect_blocking();

// Checks WiFi connection and reconnects if needed
void wifi_ensure_connected();
