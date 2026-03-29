#include "api_client.h"
#include "config.h"
#include "secrets.h"

#include <WiFi.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>

String makeUrl(const char* path) {
  return String(SERVER_BASE) + String(path);
}

static void addStandardAuthHeaders(HTTPClient& http) {
  http.addHeader("Authorization", String("Bearer ") + DEVICE_TOKEN);
  http.addHeader("Accept", "application/json");
}

bool api_register_device() {
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("api_register_device: WiFi not connected");
    return false;
  }

  const String url = makeUrl(PATH_REGISTER);
  Serial.print("Register URL: ");
  Serial.println(url);

  HTTPClient http;
  http.begin(url);

  addStandardAuthHeaders(http);
  http.addHeader("Content-Type", "application/json");

  StaticJsonDocument<192> doc;
  doc["device_id"] = DEVICE_ID;
  doc["name"] = DEVICE_NAME;
  doc["firmware_version"] = FIRMWARE_VERSION;

  String payload;
  serializeJson(doc, payload);

  Serial.print("Register payload: ");
  Serial.println(payload);

  int code = http.POST(payload);

  Serial.print("POST register status: ");
  Serial.println(code);

  if (code < 0) {
    Serial.print("HTTP error text: ");
    Serial.println(http.errorToString(code));
  }

  String resp = http.getString();
  if (resp.length() > 0) {
    Serial.println("Server response:");
    Serial.println(resp);
  }

  http.end();
  return (code >= 200 && code < 300);
}

bool api_fetch_desired(LightState& out) {
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("api_fetch_desired: WiFi not connected");
    return false;
  }

  HTTPClient http;
  http.begin(makeUrl(PATH_DESIRED));
  addStandardAuthHeaders(http);

  int code = http.GET();

  if (code != 200) {
    Serial.print("GET desired failed, HTTP code: ");
    Serial.println(code);

    String errBody = http.getString();
    if (errBody.length() > 0) {
      Serial.println("Server response:");
      Serial.println(errBody);
    }

    http.end();
    return false;
  }

  String body = http.getString();
  http.end();

  StaticJsonDocument<256> doc;
  DeserializationError err = deserializeJson(doc, body);

  if (err) {
    Serial.print("JSON parse error: ");
    Serial.println(err.c_str());
    Serial.println("Raw body:");
    Serial.println(body);
    return false;
  }

  out.is_on = doc["is_on"] | false;
  out.brightness = doc["brightness"] | 100;
  return true;
}

bool api_report_applied(const LightState& s) {
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("api_report_applied: WiFi not connected");
    return false;
  }

  HTTPClient http;
  http.begin(makeUrl(PATH_REPORT));

  addStandardAuthHeaders(http);
  http.addHeader("Content-Type", "application/json");

  StaticJsonDocument<192> doc;
  doc["device_id"] = DEVICE_ID;
  doc["is_on"] = s.is_on;
  doc["brightness"] = s.brightness;

  String payload;
  serializeJson(doc, payload);

  int code = http.POST(payload);

  Serial.print("POST report status: ");
  Serial.println(code);

  String resp = http.getString();
  if (resp.length() > 0) {
    Serial.println("Server response:");
    Serial.println(resp);
  }

  http.end();
  return (code >= 200 && code < 300);
}