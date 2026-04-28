#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>
#include <DHTesp.h>
#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include <Adafruit_NeoPixel.h>
#include <ESP32Servo.h>

// ---- WiFi ----
const char* ssid = "Wokwi-GUEST";
const char* password = "";

// ---- API endpoints ----
const char* serverUrlLocalData = "http://host.wokwi.internal:8000/data";
const char* serverUrlNgrokData = "https://94cf-196-70-248-113.ngrok-free.app/data";
const char* serverUrlLocalActuator = "http://host.wokwi.internal:8000/actuator/state";
const char* serverUrlNgrokActuator = "https://94cf-196-70-248-113.ngrok-free.app/actuator/state";

const char* deviceId = "esp32-001";

const int dhtPin = 15;
const int relayPin = 4;
const int servoPin = 13;
const int neoPixelPin = 12;
const int i2cSdaPin = 21;
const int i2cSclPin = 22;

const uint8_t oledWidth = 128;
const uint8_t oledHeight = 64;
const int stockCriticalPct = 20;
const int stockWarningPct = 40;

struct DistanceSensorConfig {
  const char* produitId;
  const char* productLabel;
  int trigPin;
  int echoPin;
};

DistanceSensorConfig distanceSensors[] = {
  {"produit-1", "Bac principal", 5, 18},
  {"produit-2", "Bac secondaire", 17, 16},
  {"produit-3", "Bac reserve", 32, 33},
};

const size_t DISTANCE_SENSOR_COUNT = sizeof(distanceSensors) / sizeof(distanceSensors[0]);

struct SensorSnapshot {
  const char* produitId;
  const char* productLabel;
  float distanceCm;
  int stockPercent;
};

WiFiClientSecure secureClient;
DHTesp dhtSensor;
Adafruit_SSD1306 oledDisplay(oledWidth, oledHeight, &Wire, -1);
Adafruit_NeoPixel statusPixel(1, neoPixelPin, NEO_GRB + NEO_KHZ800);
Servo actuatorServo;

bool oledReady = false;
String lastActuatorState = "off";
String lastActuatorMode = "auto";
float lastActuatorThreshold = 75.0f;

int mapStockFromDistance(float distanceCm) {
  int stockPercent = map((int)distanceCm, 2, 400, 100, 0);
  stockPercent = constrain(stockPercent, 0, 100);
  return stockPercent;
}

float readDistanceCm(int trigPin, int echoPin) {
  digitalWrite(trigPin, LOW);
  delayMicroseconds(2);
  digitalWrite(trigPin, HIGH);
  delayMicroseconds(10);
  digitalWrite(trigPin, LOW);

  long duration = pulseIn(echoPin, HIGH, 35000);
  if (duration <= 0) {
    return -1.0;
  }

  return (duration * 0.034f) / 2.0f;
}

float readStableDistanceCm(int trigPin, int echoPin) {
  const int sampleCount = 5;
  float samples[sampleCount];
  int validCount = 0;

  for (int i = 0; i < sampleCount; i++) {
    float value = readDistanceCm(trigPin, echoPin);
    if (value > 0.0f && value <= 400.0f) {
      samples[validCount++] = value;
    }
    delay(20);
  }

  if (validCount == 0) {
    return -1.0f;
  }

  for (int i = 0; i < validCount - 1; i++) {
    for (int j = i + 1; j < validCount; j++) {
      if (samples[j] < samples[i]) {
        float temp = samples[i];
        samples[i] = samples[j];
        samples[j] = temp;
      }
    }
  }

  return samples[validCount / 2];
}

uint32_t colorForStock(int stockPercent, bool actuatorOn) {
  if (actuatorOn) {
    return statusPixel.Color(0, 70, 180);
  }

  if (stockPercent <= stockCriticalPct) {
    return statusPixel.Color(200, 0, 0);
  }

  if (stockPercent <= stockWarningPct) {
    return statusPixel.Color(200, 120, 0);
  }

  return statusPixel.Color(0, 140, 0);
}

void updateStatusPixel(int worstStockPercent, bool actuatorOn) {
  statusPixel.setPixelColor(0, colorForStock(worstStockPercent, actuatorOn));
  statusPixel.show();
}

void renderLocalDashboard(
  const SensorSnapshot* snapshots,
  size_t count,
  bool envValid,
  float temperatureC,
  float humidityPct,
  bool wifiConnected
) {
  if (!oledReady) {
    return;
  }

  oledDisplay.clearDisplay();
  oledDisplay.setTextSize(1);
  oledDisplay.setTextColor(SSD1306_WHITE);
  oledDisplay.setCursor(0, 0);

  oledDisplay.print("WiFi: ");
  oledDisplay.println(wifiConnected ? "OK" : "OFF");

  oledDisplay.print("Act: ");
  oledDisplay.print(lastActuatorState);
  oledDisplay.print("/");
  oledDisplay.println(lastActuatorMode);

  for (size_t i = 0; i < count && i < 3; i++) {
    oledDisplay.print("P");
    oledDisplay.print(i + 1);
    oledDisplay.print(": ");
    oledDisplay.print(snapshots[i].stockPercent);
    oledDisplay.print("% ");
    oledDisplay.print(snapshots[i].distanceCm, 0);
    oledDisplay.println("cm");
  }

  if (envValid) {
    oledDisplay.print("T:");
    oledDisplay.print(temperatureC, 1);
    oledDisplay.print("C H:");
    oledDisplay.print(humidityPct, 1);
    oledDisplay.println("%");
  } else {
    oledDisplay.println("Env: indisponible");
  }

  oledDisplay.display();
}

int postJsonToUrl(const char* url, const String& jsonBody) {
  HTTPClient http;
  http.setConnectTimeout(15000);
  http.setTimeout(15000);
  http.setReuse(false);

  String urlStr = String(url);
  bool beginOk = false;

  if (urlStr.startsWith("https://")) {
    secureClient.setInsecure();
    secureClient.setTimeout(15000);
    beginOk = http.begin(secureClient, urlStr);
  } else {
    beginOk = http.begin(urlStr);
  }

  if (!beginOk) {
    Serial.println("Erreur HTTP: begin() failed");
    return -100;
  }

  http.addHeader("Content-Type", "application/json");
  int httpCode = http.POST(jsonBody);

  if (httpCode > 0) {
    Serial.print("Code HTTP POST: ");
    Serial.println(httpCode);
  } else {
    Serial.print("Erreur HTTP POST: ");
    Serial.println(httpCode);
    Serial.print("Detail: ");
    Serial.println(http.errorToString(httpCode));
  }

  http.end();
  secureClient.stop();
  return httpCode;
}

bool fetchActuatorStateFromUrl(const char* url, String& stateOut, String& modeOut, float& thresholdOut) {
  HTTPClient http;
  http.setConnectTimeout(10000);
  http.setTimeout(10000);

  String urlStr = String(url);
  bool beginOk = false;

  if (urlStr.startsWith("https://")) {
    secureClient.setInsecure();
    secureClient.setTimeout(10000);
    beginOk = http.begin(secureClient, urlStr);
  } else {
    beginOk = http.begin(urlStr);
  }

  if (!beginOk) {
    return false;
  }

  int httpCode = http.GET();
  if (httpCode <= 0) {
    http.end();
    secureClient.stop();
    return false;
  }

  if (httpCode >= 300) {
    http.end();
    secureClient.stop();
    return false;
  }

  String response = http.getString();
  http.end();
  secureClient.stop();

  JsonDocument doc;
  DeserializationError err = deserializeJson(doc, response);
  if (err) {
    return false;
  }

  stateOut = String((const char*)(doc["state"] | "off"));
  modeOut = String((const char*)(doc["mode"] | "auto"));
  thresholdOut = (float)(doc["humidity_threshold_pct"] | 75.0);
  return true;
}

void applyActuatorState(const String& state, const String& mode, float threshold) {
  bool on = state.equalsIgnoreCase("on");
  digitalWrite(relayPin, on ? HIGH : LOW);
  actuatorServo.write(on ? 90 : 0);

  lastActuatorState = on ? "on" : "off";
  lastActuatorMode = mode;
  lastActuatorThreshold = threshold;

  Serial.print("Actionneur -> etat=");
  Serial.print(on ? "ON" : "OFF");
  Serial.print(" | mode=");
  Serial.print(mode);
  Serial.print(" | seuil_humidite=");
  Serial.println(threshold, 1);
}

int sendTelemetry(const SensorSnapshot* snapshots, size_t count, bool envValid, float temperatureC, float humidityPct) {
  JsonDocument doc;
  doc["device_id"] = deviceId;
  doc["timestamp_ms"] = millis();
  doc["distance_stack"] = "tof_target_vl53l1x_wokwi_fallback_hcsr04";
  doc["env_stack"] = "bme280_target_wokwi_fallback_dht22";

  if (envValid) {
    doc["temperature_c"] = temperatureC;
    doc["humidity_pct"] = humidityPct;
  }

  JsonArray sensors = doc["sensors"].to<JsonArray>();
  for (size_t i = 0; i < count; i++) {
    JsonObject sensorObj = sensors.add<JsonObject>();
    sensorObj["produit_id"] = snapshots[i].produitId;
    sensorObj["product"] = snapshots[i].productLabel;
    sensorObj["distance_cm"] = snapshots[i].distanceCm;
    sensorObj["valeur"] = snapshots[i].stockPercent;
  }

  String jsonBody;
  serializeJson(doc, jsonBody);

  Serial.println("Envoi telemetrie multi-capteurs (local) ...");
  int httpCode = postJsonToUrl(serverUrlLocalData, jsonBody);
  if (httpCode > 0) {
    return httpCode;
  }

  Serial.println("Fallback telemetrie vers ngrok ...");
  return postJsonToUrl(serverUrlNgrokData, jsonBody);
}

void connectWifi() {
  WiFi.mode(WIFI_STA);
  WiFi.begin(ssid, password);
  Serial.print("Connexion au WiFi");

  unsigned long wifiStart = millis();
  while (WiFi.status() != WL_CONNECTED) {
    if (millis() - wifiStart > 30000) {
      Serial.println("\nErreur WiFi: timeout de connexion");
      return;
    }
    delay(500);
    Serial.print(".");
  }

  Serial.println("\nConnecte au WiFi");
  Serial.print("IP locale: ");
  Serial.println(WiFi.localIP());
}

void showBootScreen() {
  if (!oledReady) {
    return;
  }

  oledDisplay.clearDisplay();
  oledDisplay.setTextSize(1);
  oledDisplay.setTextColor(SSD1306_WHITE);
  oledDisplay.setCursor(0, 0);
  oledDisplay.println("Smart Stock Edge");
  oledDisplay.println("OLED/NeoPixel/Servo");
  oledDisplay.println("ToF+BME target mode");
  oledDisplay.println("Wokwi fallback ON");
  oledDisplay.display();
}

void setup() {
  Serial.begin(115200);

  for (size_t i = 0; i < DISTANCE_SENSOR_COUNT; i++) {
    pinMode(distanceSensors[i].trigPin, OUTPUT);
    pinMode(distanceSensors[i].echoPin, INPUT);
  }

  pinMode(relayPin, OUTPUT);
  digitalWrite(relayPin, LOW);

  actuatorServo.setPeriodHertz(50);
  actuatorServo.attach(servoPin, 500, 2400);
  actuatorServo.write(0);

  statusPixel.begin();
  statusPixel.clear();
  statusPixel.show();

  Wire.begin(i2cSdaPin, i2cSclPin);
  oledReady = oledDisplay.begin(SSD1306_SWITCHCAPVCC, 0x3C);
  if (!oledReady) {
    Serial.println("OLED indisponible.");
  }

  showBootScreen();

  dhtSensor.setup(dhtPin, DHTesp::DHT22);

  Serial.println("Mode capteurs: ToF VL53L1X + BME280 (fallback Wokwi: HC-SR04 + DHT22)");

  connectWifi();
  Serial.println("Pret a envoyer les donnees, mettre a jour OLED et piloter actionneur...");
}

void loop() {
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("WiFi deconnecte, reconnexion...");
    WiFi.disconnect();
    connectWifi();
    delay(1000);
    return;
  }

  SensorSnapshot snapshots[DISTANCE_SENSOR_COUNT];
  int worstStock = 100;

  for (size_t i = 0; i < DISTANCE_SENSOR_COUNT; i++) {
    float distance = readStableDistanceCm(distanceSensors[i].trigPin, distanceSensors[i].echoPin);
    if (distance < 0) {
      distance = 400.0;
    }

    int stockPercent = mapStockFromDistance(distance);

    snapshots[i].produitId = distanceSensors[i].produitId;
    snapshots[i].productLabel = distanceSensors[i].productLabel;
    snapshots[i].distanceCm = distance;
    snapshots[i].stockPercent = stockPercent;

    if (stockPercent < worstStock) {
      worstStock = stockPercent;
    }

    Serial.print("[Capteur ");
    Serial.print(i + 1);
    Serial.print("] produit=");
    Serial.print(distanceSensors[i].produitId);
    Serial.print(" | distance=");
    Serial.print(distance, 1);
    Serial.print(" cm | stock=");
    Serial.print(stockPercent);
    Serial.println("%");
  }

  TempAndHumidity envData = dhtSensor.getTempAndHumidity();
  bool envValid = !isnan(envData.temperature) && !isnan(envData.humidity);

  if (envValid) {
    Serial.print("Temperature: ");
    Serial.print(envData.temperature, 1);
    Serial.println(" C");

    Serial.print("Humidite: ");
    Serial.print(envData.humidity, 1);
    Serial.println(" %");
  } else {
    Serial.println("Capteur environnement indisponible.");
  }

  updateStatusPixel(worstStock, lastActuatorState.equalsIgnoreCase("on"));
  renderLocalDashboard(
    snapshots,
    DISTANCE_SENSOR_COUNT,
    envValid,
    envData.temperature,
    envData.humidity,
    WiFi.status() == WL_CONNECTED
  );

  sendTelemetry(
    snapshots,
    DISTANCE_SENSOR_COUNT,
    envValid,
    envData.temperature,
    envData.humidity
  );

  String actuatorState = "off";
  String actuatorMode = "auto";
  float actuatorThreshold = 75.0;

  bool gotActuator = fetchActuatorStateFromUrl(
    serverUrlLocalActuator,
    actuatorState,
    actuatorMode,
    actuatorThreshold
  );

  if (!gotActuator) {
    gotActuator = fetchActuatorStateFromUrl(
      serverUrlNgrokActuator,
      actuatorState,
      actuatorMode,
      actuatorThreshold
    );
  }

  if (gotActuator) {
    applyActuatorState(actuatorState, actuatorMode, actuatorThreshold);
    updateStatusPixel(worstStock, actuatorState.equalsIgnoreCase("on"));
    renderLocalDashboard(
      snapshots,
      DISTANCE_SENSOR_COUNT,
      envValid,
      envData.temperature,
      envData.humidity,
      true
    );
  } else {
    Serial.println("Impossible de recuperer l etat actionneur.");
  }

  delay(5000);
}
