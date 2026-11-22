// ColdTrust sensor node - ESP32 + SHT31 (temperature/humidity) + reed switch (door) + optional current sensor.
// NOT TESTED ON HARDWARE in this build: treat it as a starting point and test it with HARDWARE_TEST.md.
//
// What it does every minute:
//   1. reads temperature, humidity, door and (optionally) compressor current
//   2. if Wi-Fi + server are reachable: first sends any readings saved while offline (POST /ingest/batch), then this one (POST /ingest)
//   3. otherwise: saves the reading in flash (LittleFS) and sends it later, so a network outage does not leave a hole in the record
// Readings need a real time stamp: the node gets it from NTP and keeps it in the ESP32 clock while powered. Readings taken
// after a reboot but before the first NTP sync are kept with the boot time and converted once the clock is set; readings
// that were still un-stamped when the node lost power cannot be dated and are discarded at the next boot.
// NOT implemented (needs extra hardware): SMS fallback through a GSM module (call sendSmsFallback() from loop()).
//
// Libraries: "Adafruit SHT31 Library" (Library Manager). Board: "ESP32 Dev Module". Partition scheme with SPIFFS/LittleFS.
#include <WiFi.h>
#include <HTTPClient.h>
#include <Wire.h>
#include <LittleFS.h>
#include <time.h>
#include "Adafruit_SHT31.h"

const char* WIFI_SSID  = "YOUR_WIFI";
const char* WIFI_PASS  = "YOUR_PASSWORD";
const char* API_BASE   = "http://192.168.1.10:8000";   // laptop/server running api.py (no trailing slash)
const char* DEVICE_KEY = "demo-device-key";            // must equal DEVICE_KEY on the server
const int   CHAMBER_ID = 1;
const int   DOOR_PIN   = 27;                            // reed switch to GND; INPUT_PULLUP -> HIGH = door open (magnet away)
const bool  HAS_CT     = false;                         // true if a current-transformer module (e.g. ZMCT103C) is wired to CT_PIN
const int   CT_PIN     = 34;                            // ADC1 pin (ADC2 does not work while Wi-Fi is on)
const float CT_AMPS_PER_VOLT = 10.0;                    // calibrate against a clamp meter
const unsigned long PERIOD_MS = 60000;
const size_t MAX_BUFFER_BYTES = 200000;                 // roughly 3,500 readings = about 2.4 days at one per minute
const int   BATCH = 60;                                 // readings per /ingest/batch request
const char* BUF_FILE = "/buffer.csv";
const char* TMP_FILE = "/buffer.tmp";

Adafruit_SHT31 sht31;

bool timeSynced() { return time(nullptr) > 1700000000; }

bool connectWifi(unsigned long timeoutMs = 10000) {
  if (WiFi.status() != WL_CONNECTED) {
    WiFi.mode(WIFI_STA);
    WiFi.begin(WIFI_SSID, WIFI_PASS);
    unsigned long t0 = millis();
    while (WiFi.status() != WL_CONNECTED && millis() - t0 < timeoutMs) delay(250);
  }
  if (WiFi.status() == WL_CONNECTED && !timeSynced()) {
    configTime(0, 0, "pool.ntp.org", "time.google.com");          // UTC epoch seconds
    for (int i = 0; i < 20 && !timeSynced(); i++) delay(250);
  }
  return WiFi.status() == WL_CONNECTED;
}

float readCurrentAmps() {                                          // RMS of the AC waveform, about 2 mains cycles
  if (!HAS_CT) return -1.0;
  const int N = 400;
  static int s[N];
  long sum = 0;
  for (int i = 0; i < N; i++) { s[i] = analogRead(CT_PIN); sum += s[i]; delayMicroseconds(100); }
  float mean = (float)sum / N, acc = 0;
  for (int i = 0; i < N; i++) { float d = s[i] - mean; acc += d * d; }
  return sqrtf(acc / N) * (3.3f / 4095.0f) * CT_AMPS_PER_VOLT;
}

// ---------- flash buffer (append-only CSV; lines are "T,epoch,temp,hum,door,amps" or "M,millis,temp,hum,door,amps") ----------
void dropFirstLines(int n) {
  File in = LittleFS.open(BUF_FILE, FILE_READ);
  if (!in) return;
  File out = LittleFS.open(TMP_FILE, FILE_WRITE);
  int i = 0;
  while (in.available()) {
    String line = in.readStringUntil('\n');
    if (i++ >= n && line.length() > 0) out.println(line);
  }
  in.close(); out.close();
  LittleFS.remove(BUF_FILE);
  LittleFS.rename(TMP_FILE, BUF_FILE);
}

int countLines() {
  File f = LittleFS.open(BUF_FILE, FILE_READ);
  if (!f) return 0;
  int n = 0;
  while (f.available()) { f.readStringUntil('\n'); n++; }
  f.close();
  return n;
}

void purgeUnstampedLines() {                                       // at boot: boot-time ("M") lines of a previous run cannot be dated
  if (!LittleFS.exists(BUF_FILE)) return;
  File in = LittleFS.open(BUF_FILE, FILE_READ);
  File out = LittleFS.open(TMP_FILE, FILE_WRITE);
  while (in.available()) {
    String line = in.readStringUntil('\n');
    if (line.startsWith("T,")) out.println(line);
  }
  in.close(); out.close();
  LittleFS.remove(BUF_FILE);
  LittleFS.rename(TMP_FILE, BUF_FILE);
}

void bufferReading(float t, float h, bool door, float amps) {
  File f = LittleFS.open(BUF_FILE, FILE_APPEND);
  if (!f) { Serial.println("flash buffer unavailable"); return; }
  if (timeSynced()) f.printf("T,%ld,%.2f,%.1f,%d,%.2f\n", (long)time(nullptr), t, h, door ? 1 : 0, amps);
  else              f.printf("M,%lu,%.2f,%.1f,%d,%.2f\n", millis(), t, h, door ? 1 : 0, amps);
  size_t size = f.size();
  f.close();
  if (size > MAX_BUFFER_BYTES) dropFirstLines(countLines() / 2);   // flash full: drop the oldest half, keep the newest
  Serial.println("buffered reading in flash");
}

bool postJson(const char* path, const String& body) {
  HTTPClient http;
  http.begin(String(API_BASE) + path);
  http.setTimeout(8000);
  http.addHeader("Content-Type", "application/json");
  http.addHeader("X-Device-Key", DEVICE_KEY);
  int code = http.POST(body);
  http.end();
  Serial.printf("POST %s -> %d\n", path, code);
  return code == 200;
}

String readingJson(long ts, float t, float h, bool door, float amps) {
  String j = String("{\"temp_c\":") + String(t, 2) + ",\"humidity_pct\":" + String(h, 1) + ",\"door_open\":" + (door ? "true" : "false");
  if (amps >= 0) j += String(",\"compressor_current_a\":") + String(amps, 2);
  if (ts > 0) j += String(",\"ts\":") + ts;
  return j + "}";
}

bool flushBuffer() {                                               // true when the buffer is empty afterwards
  while (LittleFS.exists(BUF_FILE)) {
    if (!timeSynced()) return false;
    File f = LittleFS.open(BUF_FILE, FILE_READ);
    if (!f) return false;
    if (f.size() == 0) { f.close(); LittleFS.remove(BUF_FILE); return true; }
    String body = String("{\"chamber_id\":") + CHAMBER_ID + ",\"readings\":[";
    int lines = 0, items = 0;
    long now = (long)time(nullptr);
    while (f.available() && lines < BATCH) {
      String line = f.readStringUntil('\n');
      lines++;
      char kind; unsigned long stamp; float t, h, a; int d;
      if (sscanf(line.c_str(), "%c,%lu,%f,%f,%d,%f", &kind, &stamp, &t, &h, &d, &a) != 6) continue;
      long ts = (kind == 'T') ? (long)stamp : now - (long)((millis() - stamp) / 1000);
      if (items++) body += ",";
      body += readingJson(ts, t, h, d == 1, a);
    }
    f.close();
    body += "]}";
    if (items > 0 && !postJson("/ingest/batch", body)) return false;   // server unreachable: keep everything for the next try
    dropFirstLines(lines);
    if (!connectWifi(2000)) return false;
  }
  return true;
}

void setup() {
  Serial.begin(115200);
  pinMode(DOOR_PIN, INPUT_PULLUP);
  if (HAS_CT) analogReadResolution(12);
  sht31.begin(0x44);
  if (!LittleFS.begin(true)) Serial.println("LittleFS mount failed");
  purgeUnstampedLines();
  connectWifi();
}

void loop() {
  unsigned long started = millis();
  float t = sht31.readTemperature(), h = sht31.readHumidity();
  if (isnan(t) || isnan(h)) { Serial.println("sensor read failed"); delay(5000); return; }   // silence -> server raises a sensor-offline alert
  bool door = digitalRead(DOOR_PIN) == HIGH;
  float amps = readCurrentAmps();

  bool sent = false;
  if (connectWifi() && flushBuffer()) {                            // old readings first, so the record stays in time order
    String body = String("{\"chamber_id\":") + CHAMBER_ID + "," + readingJson(timeSynced() ? (long)time(nullptr) : 0, t, h, door, amps).substring(1);
    sent = postJson("/ingest", body);
  }
  if (!sent) bufferReading(t, h, door, amps);                      // TODO (needs GSM module): sendSmsFallback() when offline during a critical state

  unsigned long used = millis() - started;
  delay(used < PERIOD_MS ? PERIOD_MS - used : 1000);
}
