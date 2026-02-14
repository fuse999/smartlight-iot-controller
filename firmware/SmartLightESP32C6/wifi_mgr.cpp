#include "wifi_mgr.h"
#include "secrets.h"
#include <WiFi.h>

/*
  ============================================================
  wifi_connect_blocking()
  ============================================================

  Connects to WiFi using credentials defined in secrets.h.

  This function blocks execution until a connection is made.
  Used during startup.
*/
void wifi_connect_blocking() {
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


/*
  ============================================================
  wifi_ensure_connected()
  ============================================================

  Called continuously in the main loop.

  If WiFi drops, this function reconnects automatically.

  This keeps the device resilient to network interruptions.
*/
void wifi_ensure_connected() {
  if (WiFi.status() == WL_CONNECTED) return;

  Serial.println("WiFi lost, reconnecting...");
  wifi_connect_blocking();
}
