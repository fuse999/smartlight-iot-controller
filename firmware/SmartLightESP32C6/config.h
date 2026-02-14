#pragma once
#include <Arduino.h>

/*
  ============================================================
  CONFIGURATION FILE
  ============================================================

  This file contains high-level configuration values used
  throughout the firmware.

  If we need to change:
    - Polling rate
    - Device identity
    - API endpoint paths

  We only modify them here instead of hunting through code.

  Keeping configuration centralized prevents errors and
  makes the system easier to maintain.
*/

// ============================================================
// How often (in milliseconds) the device polls the server
// for a new desired light state.
//
// 1000 ms = 1 second
// ============================================================
static const uint32_t POLL_INTERVAL_MS = 1000;


// ============================================================
// Unique device identifier
//
// This allows the backend to distinguish between multiple
// ESP32 devices in the system.
// ============================================================
static const char* DEVICE_ID = "esp32c6-001";


// ============================================================
// API endpoint paths
//
// These are appended to SERVER_BASE (defined in secrets.h)
// to create full URLs for HTTP requests.
// ============================================================
static const char* PATH_DESIRED = "/api/light/desired/";
static const char* PATH_REPORT  = "/api/light/report/";
