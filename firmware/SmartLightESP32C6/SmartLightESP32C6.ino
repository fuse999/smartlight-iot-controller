// ===== Secrets / Config (put these at the TOP, not inside setup) =====
#include "secrets.h"

// ===== Includes (also at the TOP) =====
#include <WiFi.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>

// ===== Settings =====
static const uint32_t POLL_INTERVAL_MS = 1000;
static uint32_t lastPoll = 0;

// Onboard LED used for ON/OFF
#ifndef LED_BUILTIN
#define LED_BUILTIN 2
#endif

struct LightState {
  bool is_on = false;
  int brightness = 100;
};

LightState applied;

// Helper to build full URL
String makeUrl(const char* path) {
  return String(SERVER_BASE) + String(path);
}

void connectWiFi() {
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASS);

  Serial.print("Connecting to WiFi");
  while (WiFi.status() != WL_CONNECTED) {
    delay(400);
    Serial.print(".");
  }
  Serial.println();
  Serial.print("Connected. IP: ");
  Serial.println(WiFi.localIP());
}

bool statesEqual(const LightState& a, const LightState& b) {
  return a.is_on == b.is_on && a.brightness == b.brightness;
}

void applyState(const LightState& s) {
  digitalWrite(LED_BUILTIN, s.is_on ? HIGH : LOW);
  applied = s;
}

bool fetchDesired(LightState& out) {
  if (WiFi.status() != WL_CONNECTED) return false;

  HTTPClient http;
  http.begin(makeUrl("/api/light/desired/"));
  http.addHeader("Authorization", String("Bearer ") + DEVICE_TOKEN);
  http.addHeader("Accept", "application/json");

  int code = http.GET();
  if (code != 200) {
    Serial.print("GET failed: ");
    Serial.println(code);
    http.end();
    return false;
  }

  String body = http.getString();
  http.end();

  StaticJsonDocument<256> doc;
  auto err = deserializeJson(doc, body);
  if (err) {
    Serial.print("JSON parse error: ");
    Serial.println(err.c_str());
    return false;
  }

  out.is_on = doc["is_on"] | false;
  out.brightness = doc["brightness"] | 100;
  return true;
}

void reportApplied(const LightState& s) {
  if (WiFi.status() != WL_CONNECTED) return;



  HTTPClient http;
  http.begin(makeUrl("/api/light/report/"));
  http.addHeader("Authorization", String("Bearer ") + DEVICE_TOKEN);
  http.addHeader("Content-Type", "application/json");

  StaticJsonDocument<128> doc;
  doc["device_id"] = "esp32c6-001";
  doc["is_on"] = s.is_on;
  doc["brightness"] = s.brightness;

  String payload;
  serializeJson(doc, payload);

  int code = http.POST(payload);
  Serial.print("POST report: ");
  Serial.println(code);
  
  String body = http.getString();
  Serial.println(body);

  http.end();
}

void setup() {
  Serial.begin(115200);
  delay(200);

  pinMode(LED_BUILTIN, OUTPUT);
  digitalWrite(LED_BUILTIN, LOW);

  connectWiFi();
}

void loop() {
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("WiFi lost, reconnecting...");
    connectWiFi();
  }

  uint32_t now = millis();
  if (now - lastPoll >= POLL_INTERVAL_MS) {
    lastPoll = now;

    LightState desired;
    if (fetchDesired(desired)) {
      if (!statesEqual(desired, applied)) {
        Serial.print("Applying: on=");
        Serial.print(desired.is_on);
        Serial.print(" brightness=");
        Serial.println(desired.brightness);

        applyState(desired);
        reportApplied(desired);
      }
    }
  }

  delay(10);
}
