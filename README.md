# Smart Stock IoT - InfluxDB 3 Edition

Systeme intelligent de gestion de stock IoT avec:
- ESP32 (Wokwi) multi-capteurs distance + interface locale OLED/NeoPixel
- FastAPI backend
- InfluxDB 3 (IOx) pour la telemetrie time-series
- React dashboard temps reel (WebSocket)
- Actionneur pilote par seuil d humidite (relais + servo)
- Analyse predictive de rupture (regression lineaire)
- Alertes multi-canaux (UI + email + webhook + Telegram Bot)

## Architecture

- esp32/: firmware Wokwi (distance stabilisee type ToF, env type BME target, OLED SSD1306, NeoPixel, relais + servo)
- backend/: API FastAPI + moteur de regles + integration InfluxDB 3
- frontend/: dashboard React (charts, alertes, controle actionneur)
- db/: ancien SQL MySQL (historique, non utilise dans cette version)

## Upgrade materiel (Wokwi)

- Cible projet: VL53L1X (ToF) + BME280
- Limitation Wokwi actuelle: ces capteurs ne sont pas exposes comme parts natives dans la liste officielle.
- Strategie appliquee: fallback simulation sur HC-SR04 + DHT22 avec filtrage median pour une distance plus stable.
- Composants ajoutes en simulation:
  - OLED SSD1306 (I2C, affichage local de l etat)
  - NeoPixel WS2812B (etat visuel global)
  - Servo (position ON/OFF)
  - Relais (commutation ON/OFF)

## Schema InfluxDB 3

Measurement: stock_data

Tags:
- produit_id
- device_id (optionnel)
- product

Fields:
- distance (float)
- humidite (float)
- etat_relais (int: 0/1)
- angle_servo (int: 0/90)
- valeur (int, optionnel)
- temperature_c (float)

Time column:
- time (timestamp)

## Variables d environnement backend

- INFLUX_URL (defaut: http://localhost:8181)
- INFLUX_DATABASE (defaut: smart_stock)
- INFLUX_BUCKET (defaut: smart_stock)
- INFLUX_ORG (optionnel, utile pour endpoint v2)
- INFLUX_TOKEN (optionnel)
- INFLUX_TIMEOUT_SECONDS (defaut: 8)

Alertes:
- ALERT_STOCK_WARNING
- ALERT_STOCK_CRITICAL
- ALERT_TEMP_WARNING_C
- ALERT_TEMP_CRITICAL_C
- ALERT_HUMIDITY_LOW_WARNING
- ALERT_HUMIDITY_HIGH_WARNING
- ALERT_HUMIDITY_LOW_CRITICAL
- ALERT_HUMIDITY_HIGH_CRITICAL
- ALERT_COMBINATION_TEMP_BOOST_C
- ALERT_COOLDOWN_SECONDS

Prediction:
- PREDICTIVE_ALERT_DAYS (defaut 3)

Notifications Telegram (optionnel):
- TELEGRAM_TOKEN
- TELEGRAM_CHAT_ID

Actionneur:
- ACTUATOR_MODE (auto | manual, defaut auto)
- ACTUATOR_HUMIDITY_THRESHOLD_PCT (defaut 75)

## Payload capteur accepte

Ancien mode (retrocompatible):

{
  "valeur": 42
}

Mode multi-capteurs recommande:

{
  "device_id": "esp32-001",
  "timestamp_ms": 1730000012345,
  "temperature_c": 30.6,
  "humidity_pct": 67.2,
  "sensors": [
    {
      "produit_id": "produit-1",
      "product": "Bac principal",
      "distance_cm": 86.5,
      "valeur": 76
    },
    {
      "produit_id": "produit-2",
      "product": "Bac secondaire",
      "distance_cm": 122.3,
      "valeur": 62
    },
    {
      "produit_id": "produit-3",
      "product": "Bac reserve",
      "distance_cm": 150.2,
      "valeur": 55
    }
  ]
}

## Endpoints API

Telemetrie:
- POST /data
- GET /data?limit=120&produit_id=produit-1
- GET /products
- GET /prediction?produit_id=produit-1

Alertes:
- GET /alerts
- POST /alerts/{alert_id}/ack
- GET /alerts/config
- POST /alerts/config

Actionneur:
- GET /actuator/state
- POST /actuator/config
- POST /actuator/command
  - command: auto | on | off | force_ventilation

Observabilite:
- GET /health
- GET /logs
- GET /logs/raw
- POST /arduino-log
- GET /arduino-logs
- GET /arduino-logs/raw

Temps reel:
- WS /ws
  - telemetry
  - telemetry_batch
  - alert
  - actuator
  - heartbeat

## Prediction de rupture (Smart)

Le backend calcule une regression lineaire sur l historique temporel de chaque produit.

Exemple de sortie /prediction:

{
  "status": "ok",
  "produit_id": "produit-1",
  "product": "Bac principal",
  "days_to_empty": 2.8,
  "estimated_empty_at": "2026-04-19 14:10:00",
  "consumption_rate_pct_per_day": 17.5,
  "model_r2": 0.94,
  "risk_level": "Alerte",
  "risk_window_days": 3,
  "message": "Au rythme actuel, rupture estimee dans 2.8 jours"
}

Quand la rupture estimee est proche (<= PREDICTIVE_ALERT_DAYS), une alerte proactive est generee avec type rupture_prevue.

## Guide de Démarrage et Utilisation

Ce guide détaille les étapes pour lancer les différentes briques du projet depuis des terminaux distincts.

### Étape 1 : InfluxDB 3
Assurez-vous qu'InfluxDB 3 est lancé et accessible.
- **Port par défaut :** `8181`
- **Vérification (PowerShell) :** 
  ```powershell
  Invoke-WebRequest -Uri "http://127.0.0.1:8181/health" -Method Get
  ```

### Étape 2 : API Backend (FastAPI)
Ouvrez un **deuxième terminal** pour le backend.
```powershell
cd backend
python -m venv venv
.\venv\Scripts\Activate
pip install -r requirements.txt
cd ..
python start_api.py
```
- **URL Backend :** http://127.0.0.1:8000
- **Test de santé de l'API :**
  ```powershell
  Invoke-RestMethod -Uri "http://127.0.0.1:8000/health" -Method Get
  ```

### Étape 3 : Frontend (React)
Ouvrez un **troisième terminal** pour l'interface React.
```powershell
cd frontend
npm install
npm run dev
```
- Le dashboard s'ouvrira généralement sur http://localhost:5173 (vérifiez les logs Node.js).

### Étape 4 : Simulation ESP32 (Wokwi & PlatformIO)
Ouvrez un **quatrième terminal** pour compiler le firmware Wokwi.
```powershell
cd esp32
# 1. Installer PlatformIO
python -m pip install platformio
# 2. Compiler le code
python -m platformio run
# 3. Fusionner les binaires pour le simulateur
python -m platformio pkg exec -p tool-esptoolpy -- esptool.py --chip esp32 merge_bin -o .pio/build/esp32dev/firmware.merged.bin --flash_mode dio --flash_freq 40m --flash_size 4MB 0x1000 .pio/build/esp32dev/bootloader.bin 0x8000 .pio/build/esp32dev/partitions.bin 0x10000 .pio/build/esp32dev/firmware.bin
```
**Dans VS Code :**
1. Arrêtez le simulateur Wokwi si un tourne : `Wokwi: Stop Simulator`.
2. Lancez le simulateur : `Wokwi: Start Simulator`.
3. Ouvrez le terminal série depuis l'interface Wokwi.

### Étape 5 (Optionnelle) : Tunnel ngrok
Si l'ESP32 dans Wokwi a besoin d'accéder au backend local depuis le cloud public, ouvrez un **cinquième terminal** :
```powershell
ngrok http 8000
```
- *Notes :* Si l'URL ngrok change, mettez à jour `serverUrlNgrokData` et `serverUrlNgrokActuator` dans le fichier `esp32/sketch.ino`.

### Tests Rapides et Débogage

**Logs de l'APIBackend (PowerShell) :**
```powershell
Invoke-RestMethod -Uri "http://127.0.0.1:8000/logs/raw?lines=200" -Method Get
```

**Forcer manuellement la ventilation :**
```powershell
Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/actuator/command" -ContentType "application/json" -Body '{"command":"force_ventilation"}'
```

Puis relancer le simulateur Wokwi.

### 4. Tunnel public (optionnel pour Wokwi web)

ngrok http 8000

Remplacer les URLs ngrok dans esp32/sketch.ino.

## Exemples SQL InfluxDB 3

Derniers points:
SELECT time, produit_id, product, valeur, humidite, etat_relais, angle_servo
FROM stock_data
ORDER BY time DESC
LIMIT 50;

Filtre par produit:
SELECT time, valeur, distance, humidite
FROM stock_data
WHERE produit_id = 'produit-1'
ORDER BY time DESC
LIMIT 100;
