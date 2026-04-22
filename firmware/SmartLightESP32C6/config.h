#pragma once
#include <Arduino.h>

static const uint32_t POLL_INTERVAL_MS = 1000;

// Power sampling/reporting behavior
static const uint32_t POWER_SAMPLE_INTERVAL_MS = 2000;
static const uint32_t POWER_REPORT_ON_INTERVAL_MS = 10000;
static const uint32_t POWER_REPORT_OFF_INTERVAL_MS = 60000;

// Power estimation settings
static const float ESTIMATED_LINE_VOLTAGE = 120.0f;
static const float POWER_SMOOTHING_ALPHA = 0.20f;

static const char* DEVICE_ID = "xiao-esp32c6-001";
static const char* DEVICE_NAME = "DIY Smart Light Controller";
static const char* FIRMWARE_VERSION = "0.2.0";

static const char* PATH_REGISTER = "/api/device/register/";
static const char* PATH_DESIRED  = "/api/device/desired/";
static const char* PATH_REPORT   = "/api/device/report/";