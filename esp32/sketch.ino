#include <WiFi.h>
#include <PubSubClient.h>
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

// ---- MQTT ----
const char* mqtt_server = "broker.hivemq.com";
const char* mqtt_topic_data = "smartstock/ela35/data/esp32-001";
const char* mqtt_topic_actuator = "smartstock/ela35/actuator/esp32-001";

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

WiFiClient espClient;
PubSubClient client(espClient);
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

void callback(char* topic, byte* payload, unsigned int length) {
  String message;
  for (int i = 0; i < length; i++) {
    message += (char)payload[i];
  }
  
  if (String(topic) == mqtt_topic_actuator) {
    JsonDocument doc;
    DeserializationError err = deserializeJson(doc, message);
    if (!err) {
      String stateOut = String((const char*)(doc["state"] | "off"));
      String modeOut = String((const char*)(doc["mode"] | "auto"));
      float thresholdOut = (float)(doc["humidity_threshold_pct"] | 75.0);
      applyActuatorState(stateOut, modeOut, thresholdOut);
    }
  }
}

void reconnect() {
  while (!client.connected()) {
    Serial.print("Attempting MQTT connection...");
    if (client.connect("ESP32Client_SmartStock")) {
      Serial.println("connected");
      client.subscribe(mqtt_topic_actuator);
    } else {
      Serial.print("failed, rc=");
      Serial.print(client.state());
      Serial.println(" try again in 5 seconds");
      delay(5000);
    }
  }
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

  Serial.println("Envoi telemetrie via MQTT...");
  if (client.publish(mqtt_topic_data, jsonBody.c_str())) {
    return 200;
  } else {
    Serial.println("Erreur de publication MQTT");
    return -1;
  }
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
  client.setServer(mqtt_server, 1883);
  client.setCallback(callback);
  client.setBufferSize(1024);
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

  if (!client.connected()) {
    reconnect();
  }
  client.loop();

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

  delay(5000);
}
