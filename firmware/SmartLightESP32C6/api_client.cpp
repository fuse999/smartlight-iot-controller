/*
  ============================================================
  API CLIENT (IMPLEMENTATION)
  ============================================================

  Implements:
    - GET /api/light/desired/
    - POST /api/light/report/

  Requirements:
    - WiFi must be connected
    - secrets.h must define:
        WIFI_SSID
        WIFI_PASS
        SERVER_BASE
        DEVICE_TOKEN
    - config.h must define:
        DEVICE_ID
        PATH_DESIRED
        PATH_REPORT
*/

#include "api_client.h"
#include "config.h"
#include "secrets.h"

#include <WiFi.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>


// ============================================================
// makeUrl()
// ============================================================
//
// Concatenates the server base URL with a path.
//
// Example:
//   SERVER_BASE = "http://192.168.1.111:8000"
//   path       = "/api/light/desired/"
//
// Result:
//   "http://192.168.1.111:8000/api/light/desired/"
//
String makeUrl(const char* path) {
  return String(SERVER_BASE) + String(path);
}


// ============================================================
// api_fetch_desired()
// ============================================================
//
// Sends a GET request to PATH_DESIRED to retrieve the desired
// light state.
//
// Expected JSON response example:
//   {
//     "is_on": true,
//     "brightness": 75
//   }
//
// Output:
//   Fills the 'out' LightState struct and returns true on success.
//
bool api_fetch_desired(LightState& out) {
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("api_fetch_desired: WiFi not connected");
    return false;
  }

  HTTPClient http;

  // Build URL to desired endpoint
  http.begin(makeUrl(PATH_DESIRED));

  // Auth header (Bearer token used to identify device)
  http.addHeader("Authorization", String("Bearer ") + DEVICE_TOKEN);

  // Inform server we want JSON back
  http.addHeader("Accept", "application/json");

  int code = http.GET();

  if (code != 200) {
    Serial.print("GET desired failed, HTTP code: ");
    Serial.println(code);

    // Helpful for debugging API responses
    String errBody = http.getString();
    if (errBody.length() > 0) {
      Serial.println("Server response:");
      Serial.println(errBody);
    }

    http.end();
    return false;
  }

  // Read response body as string
  String body = http.getString();
  http.end();

  // Parse JSON response
  StaticJsonDocument<256> doc;
  DeserializationError err = deserializeJson(doc, body);

  if (err) {
    Serial.print("JSON parse error: ");
    Serial.println(err.c_str());
    Serial.println("Raw body:");
    Serial.println(body);
    return false;
  }

  // Extract fields with fallback defaults if missing
  out.is_on = doc["is_on"] | false;
  out.brightness = doc["brightness"] | 100;

  return true;
}


// ============================================================
// api_report_applied()
// ============================================================
//
// Sends a POST request to PATH_REPORT to report back the
// applied state.
//
// Payload example:
//   {
//     "device_id": "esp32c6-001",
//     "is_on": true,
//     "brightness": 75
//   }
//
// Returns true on 2xx success codes.
//
bool api_report_applied(const LightState& s) {
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("api_report_applied: WiFi not connected");
    return false;
  }

  HTTPClient http;
  http.begin(makeUrl(PATH_REPORT));

  // Auth header (Bearer token used to identify device)
  http.addHeader("Authorization", String("Bearer ") + DEVICE_TOKEN);

  // We are sending JSON in the body
  http.addHeader("Content-Type", "application/json");

  // Build JSON payload
  StaticJsonDocument<192> doc;
  doc["device_id"] = DEVICE_ID;
  doc["is_on"] = s.is_on;
  doc["brightness"] = s.brightness;

  String payload;
  serializeJson(doc, payload);

  int code = http.POST(payload);

  Serial.print("POST report status: ");
  Serial.println(code);

  // Print server response (useful during development)
  String resp = http.getString();
  if (resp.length() > 0) {
    Serial.println("Server response:");
    Serial.println(resp);
  }

  http.end();

  // 200–299 = success
  return (code >= 200 && code < 300);
}
