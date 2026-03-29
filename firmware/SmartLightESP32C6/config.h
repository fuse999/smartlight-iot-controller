#pragma once
#include <Arduino.h>

static const uint32_t POLL_INTERVAL_MS = 1000;

static const char* DEVICE_ID = "xiao-esp32c6-001";
static const char* DEVICE_NAME = "DIY Smart Light Controller";
static const char* FIRMWARE_VERSION = "0.1.0";

static const char* PATH_REGISTER = "/api/device/register/";
static const char* PATH_DESIRED  = "/api/device/desired/";
static const char* PATH_REPORT   = "/api/device/report/";