#include <Arduino.h>
#include <ArduinoJson.h>
#include <ESP32Servo.h>
#include <HTTPClient.h>
#include <LiquidCrystal_I2C.h>
#include <MFRC522.h>
#include <SPI.h>
#include <WebServer.h>
#include <Wire.h>
#include <WiFi.h>

const char* WIFI_SSID = "YOUR_WIFI";
const char* WIFI_PASSWORD = "YOUR_PASSWORD";
const char* PI_BASE_URL = "http://192.168.1.50:8000";
const char* DOOR_ID = "door-01";
const char* DEVICE_API_KEY = "change-me";

const int NFC_RST_PIN = 14;
const int NFC_SS_PIN = 10;
const int SERVO_PIN = 4;
const int BUZZER_PIN = 5;
const int BUTTON_PIN = 6;  // Button connects to GND, uses INPUT_PULLUP.
const int LCD_SDA_PIN = 8;
const int LCD_SCL_PIN = 9;
const int LCD_ADDR = 0x27;
const int SERVO_CLOSED_ANGLE = 0;
const int SERVO_OPEN_ANGLE = 90;

WebServer server(80);
Servo lockServo;
MFRC522 nfc(NFC_SS_PIN, NFC_RST_PIN);
LiquidCrystal_I2C lcd(LCD_ADDR, 16, 2);
const char* AUTH_HEADER_KEYS[] = {"X-API-Key"};

bool physicalButtonEnabled = true;
bool allowOfflineMasterCard = false;
int unlockDurationMs = 3000;
int dualAuthTimeoutMs = 3000;
unsigned long lastHeartbeat = 0;
unsigned long lastConfigFetch = 0;
unsigned long lastButtonMs = 0;
unsigned long waitingUntilMs = 0;
bool unlocking = false;

String fitLcd(String value) {
  value.trim();
  if (value.length() > 16) return value.substring(0, 16);
  while (value.length() < 16) value += " ";
  return value;
}

void showLcd(const String& line1, const String& line2 = "") {
  lcd.clear();
  lcd.setCursor(0, 0);
  lcd.print(fitLcd(line1));
  lcd.setCursor(0, 1);
  lcd.print(fitLcd(line2));
}

void showClosed() {
  waitingUntilMs = 0;
  showLcd("CLOSE", WiFi.localIP().toString());
}

void showOpen(const String& name, const String& employeeCode) {
  waitingUntilMs = 0;
  showLcd("OPEN", name.length() ? name : employeeCode);
}

void showWaiting(const String& name, const String& employeeCode) {
  waitingUntilMs = millis() + dualAuthTimeoutMs;
  showLcd("WAITING AUTH", name.length() ? name : employeeCode);
}

void showDenied() {
  waitingUntilMs = 0;
  showLcd("UNAUTHORIZED", "ACCESS DENIED");
}

void beepPattern(int count) {
  for (int i = 0; i < count; i++) {
    tone(BUZZER_PIN, 2200, 120);
    delay(170);
    noTone(BUZZER_PIN);
    delay(80);
  }
}

void beepSuccess() {
  beepPattern(1);
}

void beepDenied() {
  beepPattern(2);
}

void beepWaiting() {
  beepPattern(2);
}

void localUnlock(int durationMs) {
  if (unlocking) return;
  unlocking = true;
  beepSuccess();
  lockServo.write(SERVO_OPEN_ANGLE);
  delay(durationMs);
  lockServo.write(SERVO_CLOSED_ANGLE);
  showClosed();
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
    dualAuthTimeoutMs = (doc["dual_auth_timeout_sec"] | 3) * 1000;
    allowOfflineMasterCard = doc["allow_offline_master_card"] | allowOfflineMasterCard;
  }
  http.end();
}

void handleUnlock() {
  JsonDocument doc;
  DeserializationError err = deserializeJson(doc, server.arg("plain"));
  if (err) {
    server.send(400, "application/json", "{\"ok\":false,\"error\":\"bad_json\"}");
    return;
  }
  String headerKey = server.header("X-API-Key");
  String bodyKey = doc["signature"] | "";
  if (headerKey != DEVICE_API_KEY && bodyKey != DEVICE_API_KEY) {
    server.send(401, "application/json", "{\"ok\":false}");
    return;
  }
  int duration = doc["duration_ms"] | unlockDurationMs;
  String fullName = doc["full_name"] | "";
  String studentCode = doc["student_code"] | doc["employee_code"] | "";
  showOpen(fullName, studentCode);
  server.send(200, "application/json", "{\"ok\":true}");
  localUnlock(duration);
}

void handleNotify() {
  JsonDocument doc;
  DeserializationError err = deserializeJson(doc, server.arg("plain"));
  if (err) {
    server.send(400, "application/json", "{\"ok\":false,\"error\":\"bad_json\"}");
    return;
  }
  String headerKey = server.header("X-API-Key");
  String bodyKey = doc["signature"] | "";
  if (headerKey != DEVICE_API_KEY && bodyKey != DEVICE_API_KEY) {
    server.send(401, "application/json", "{\"ok\":false}");
    return;
  }
  String status = doc["status"] | "";
  String fullName = doc["full_name"] | "";
  String employeeCode = doc["student_code"] | doc["employee_code"] | "";
  if (status == "waiting") {
    showWaiting(fullName, employeeCode);
    beepWaiting();
  } else if (status == "denied") {
    showDenied();
    beepDenied();
  } else {
    showLcd("STATUS", status);
  }
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
    String reason = res["reason"] | "";
    bool suppressFeedback = res["suppress_feedback"] | false;
    String fullName = res["student"]["full_name"] | res["user"]["full_name"] | "";
    String employeeCode = res["student"]["student_code"] | res["user"]["employee_code"] | "";
    if (res["should_unlock"] == true) {
      showOpen(fullName, employeeCode);
      localUnlock(unlockDurationMs);
    } else if (reason == "waiting_for_second_factor") {
      if (!suppressFeedback) {
        showWaiting(fullName, employeeCode);
        beepWaiting();
      }
    } else if (reason == "card_enrolled") {
      showLcd("NFC ENROLLED", fullName.length() ? fullName : employeeCode);
      beepSuccess();
    } else {
      showDenied();
      beepDenied();
    }
  } else {
    showLcd("PI OFFLINE", "NFC FAILED");
    beepDenied();
  }
  nfc.PICC_HaltA();
}

void readButton() {
  bool pressed = digitalRead(BUTTON_PIN) == LOW;
  if (!pressed || millis() - lastButtonMs < 400) return;
  lastButtonMs = millis();
  if (physicalButtonEnabled) {
    showOpen("EXIT BUTTON", "");
    localUnlock(unlockDurationMs);
  }
  postJson(String("/api/device/") + DOOR_ID + "/button-event", "{\"event\":\"button_pressed\"}");
}

void setup() {
  Serial.begin(115200);
  pinMode(BUTTON_PIN, INPUT_PULLUP);
  pinMode(BUZZER_PIN, OUTPUT);
  Wire.begin(LCD_SDA_PIN, LCD_SCL_PIN);
  lcd.init();
  lcd.backlight();
  showLcd("BOOTING", "ESP32-S3");
  lockServo.attach(SERVO_PIN);
  lockServo.write(SERVO_CLOSED_ANGLE);
  SPI.begin();
  nfc.PCD_Init();
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  showLcd("CONNECT WIFI", WIFI_SSID);
  server.collectHeaders(AUTH_HEADER_KEYS, 1);
  server.on("/unlock", HTTP_POST, handleUnlock);
  server.on("/notify", HTTP_POST, handleNotify);
  server.on("/status", HTTP_GET, handleStatus);
  server.begin();
  showClosed();
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
  if (waitingUntilMs && millis() > waitingUntilMs && !unlocking) {
    showClosed();
  }
}
