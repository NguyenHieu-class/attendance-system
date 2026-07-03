#include <Arduino.h>
#include <ArduinoJson.h>
#include <ESP32Servo.h>
#include <HTTPClient.h>
#include <MFRC522.h>
#include <SPI.h>
#include <WebServer.h>
#include <WiFi.h>

const char* WIFI_SSID = "YOUR_WIFI";
const char* WIFI_PASSWORD = "YOUR_PASSWORD";
const char* PI_BASE_URL = "http://192.168.1.50:8000";
const char* DOOR_ID = "door-01";
const char* DEVICE_API_KEY = "change-me";

const int SERVO_PIN = 18;
const int BUTTON_PIN = 4;
const int NFC_SS_PIN = 10;
const int NFC_RST_PIN = 9;

WebServer server(80);
Servo lockServo;
MFRC522 nfc(NFC_SS_PIN, NFC_RST_PIN);

bool physicalButtonEnabled = true;
bool allowOfflineMasterCard = false;
int unlockDurationMs = 3000;
unsigned long lastHeartbeat = 0;
unsigned long lastConfigFetch = 0;
unsigned long lastButtonMs = 0;
bool unlocking = false;

void localUnlock(int durationMs) {
  if (unlocking) return;
  unlocking = true;
  lockServo.write(90);
  delay(durationMs);
  lockServo.write(0);
  unlocking = false;
}

bool postJson(const String& path, const String& body, String* responseBody = nullptr) {
  if (WiFi.status() != WL_CONNECTED) return false;
  HTTPClient http;
  http.begin(String(PI_BASE_URL) + path);
  http.addHeader("Content-Type", "application/json");
  http.addHeader("X-API-Key", DEVICE_API_KEY);
  int code = http.POST(body);
  if (responseBody) *responseBody = http.getString();
  http.end();
  return code >= 200 && code < 300;
}

void fetchConfig() {
  if (WiFi.status() != WL_CONNECTED) return;
  HTTPClient http;
  http.begin(String(PI_BASE_URL) + "/api/device/" + DOOR_ID + "/config");
  http.addHeader("X-API-Key", DEVICE_API_KEY);
  int code = http.GET();
  if (code == 200) {
    JsonDocument doc;
    deserializeJson(doc, http.getString());
    physicalButtonEnabled = doc["physical_button_enabled"] | physicalButtonEnabled;
    unlockDurationMs = doc["unlock_duration_ms"] | unlockDurationMs;
    allowOfflineMasterCard = doc["allow_offline_master_card"] | allowOfflineMasterCard;
  }
  http.end();
}

void handleUnlock() {
  if (server.header("X-API-Key") != DEVICE_API_KEY) {
    server.send(401, "application/json", "{\"ok\":false}");
    return;
  }
  JsonDocument doc;
  deserializeJson(doc, server.arg("plain"));
  int duration = doc["duration_ms"] | unlockDurationMs;
  localUnlock(duration);
  server.send(200, "application/json", "{\"ok\":true}");
}

void handleStatus() {
  JsonDocument doc;
  doc["door_id"] = DOOR_ID;
  doc["status"] = WiFi.status() == WL_CONNECTED ? "online" : "wifi_offline";
  doc["locked"] = !unlocking;
  String out;
  serializeJson(doc, out);
  server.send(200, "application/json", out);
}

void sendHeartbeat() {
  postJson(String("/api/device/") + DOOR_ID + "/heartbeat", "{}");
}

void readNfc() {
  if (!nfc.PICC_IsNewCardPresent() || !nfc.PICC_ReadCardSerial()) return;
  String uid = "";
  for (byte i = 0; i < nfc.uid.size; i++) {
    if (nfc.uid.uidByte[i] < 0x10) uid += "0";
    uid += String(nfc.uid.uidByte[i], HEX);
  }
  uid.toUpperCase();
  JsonDocument doc;
  doc["uid"] = uid;
  String body;
  serializeJson(doc, body);
  String response;
  if (postJson(String("/api/device/") + DOOR_ID + "/nfc-scan", body, &response)) {
    JsonDocument res;
    deserializeJson(res, response);
    if (res["should_unlock"] == true) localUnlock(unlockDurationMs);
  }
  nfc.PICC_HaltA();
}

void readButton() {
  bool pressed = digitalRead(BUTTON_PIN) == LOW;
  if (!pressed || millis() - lastButtonMs < 400) return;
  lastButtonMs = millis();
  if (physicalButtonEnabled) localUnlock(unlockDurationMs);
  postJson(String("/api/device/") + DOOR_ID + "/button-event", "{\"event\":\"button_pressed\"}");
}

void setup() {
  Serial.begin(115200);
  pinMode(BUTTON_PIN, INPUT_PULLUP);
  lockServo.attach(SERVO_PIN);
  lockServo.write(0);
  SPI.begin();
  nfc.PCD_Init();
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  server.on("/unlock", HTTP_POST, handleUnlock);
  server.on("/status", HTTP_GET, handleStatus);
  server.begin();
}

void loop() {
  server.handleClient();
  readNfc();
  readButton();
  if (millis() - lastHeartbeat > 15000) {
    lastHeartbeat = millis();
    sendHeartbeat();
  }
  if (millis() - lastConfigFetch > 45000) {
    lastConfigFetch = millis();
    fetchConfig();
  }
}
