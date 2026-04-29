# Smart Stock IoT - InfluxDB 3 Edition

Systeme IoT de gestion de stock avec telemetrie temps reel, moteur de regles, prediction de rupture et actionneur automatise.

## Vue d ensemble

- ESP32 simule sous Wokwi avec multi-capteurs, OLED, NeoPixel, relais et servo.
- Communication asynchrone via **MQTT** (broker public `broker.hivemq.com`).
- Backend FastAPI pour recevoir la telemetrie, generer des alertes et piloter l actionneur.
- InfluxDB 3 pour stocker les donnees time-series.
- Frontend React/Vite pour le dashboard temps reel en WebSocket.
- Prediction lineaire pour estimer la rupture de stock.
- Notifications multi-canaux via UI, email, webhook et Telegram.

## Structure du projet

- `esp32/` : firmware Wokwi, diagramme et configuration PlatformIO.
- `backend/` : API FastAPI, regles metier, alertes et integration InfluxDB 3.
- `frontend/` : dashboard React temps reel.
- `db/` : ancien schema SQL conserve pour reference.
- `start_api.py` : lanceur Python du backend.
- `start_project_commands.txt` : resume rapide des commandes de demarrage.

## Fonctionnalites

- reception de payload legacy `{"valeur": 42}` ou multi-capteurs.
- enregistrement des mesures dans `stock_data`.
- regles sur stock, temperature, humidite et combinaison critique.
- commande manuelle ou automatique de l actionneur.
- historique, alertes acquittables et logs.
- prediction de rupture avec seuil d alerte proactive.

## Prerequis

- Python 3.10+.
- Node.js 18+.
- InfluxDB 3 accessible en local sur `http://127.0.0.1:8181`.
- PlatformIO pour compiler le firmware ESP32.
- Wokwi dans VS Code si vous voulez lancer la simulation.

## Demarrage rapide

L ordre recommande est: InfluxDB 3, backend, frontend, firmware ESP32, puis tunnel optionnel si besoin.

### 1. InfluxDB 3

Verifiez que le service repond sur le port `8181`.

```powershell
Invoke-WebRequest -Uri "http://127.0.0.1:8181/health" -Method Get
```

### 2. Backend FastAPI

Depuis la racine du projet, lancez:

```powershell
cd "C:\Users\ela35\OneDrive\Documents\cour\IOT\projet\backend"
python -m venv venv
.\venv\Scripts\Activate
pip install -r requirements.txt
cd ..
python start_api.py
```

Le backend demarre sur `http://127.0.0.1:8000`.

Test rapide:

```powershell
Invoke-RestMethod -Uri "http://127.0.0.1:8000/health" -Method Get
```

### 3. Frontend React

```powershell
cd "C:\Users\ela35\OneDrive\Documents\cour\IOT\projet\frontend"
npm install
npm run dev
```

Le dashboard Vite est generalement disponible sur `http://localhost:5173`.

### 4. Firmware ESP32 / Wokwi

```powershell
cd "C:\Users\ela35\OneDrive\Documents\cour\IOT\projet\esp32"
python -m pip install platformio
python -m platformio run
python -m platformio pkg exec -p tool-esptoolpy -- esptool.py --chip esp32 merge_bin -o .pio/build/esp32dev/firmware.merged.bin --flash_mode dio --flash_freq 40m --flash_size 4MB 0x1000 .pio/build/esp32dev/bootloader.bin 0x8000 .pio/build/esp32dev/partitions.bin 0x10000 .pio/build/esp32dev/firmware.bin
```

Dans VS Code:

1. `Wokwi: Stop Simulator` si une session tourne deja.
2. `Wokwi: Start Simulator` pour relancer la simulation.
3. Ouvrez le terminal serie du simulateur pour suivre les logs.

### 5. Tunnel public optionnel (Frontend / Webhooks)

Si vous avez besoin d acceder au frontend React depuis l exterieur ou de configurer des webhooks, vous pouvez ouvrir un tunnel ngrok:

```powershell
cd "C:\Users\ela35\OneDrive\Documents\cour\IOT\projet"
ngrok http 8000
```
Note : L'ESP32 n'utilise plus ngrok car il communique directement avec le broker MQTT public `broker.hivemq.com`.

## Comment tester manuellement (MQTT)

Le backend et l'ESP32 ecoutent et publient sur des topics MQTT. Vous pouvez utiliser un client comme [MQTT Explorer](http://mqtt-explorer.com/) ou des scripts Python pour tester:

1. **Broker** : `broker.hivemq.com` (Port `1883`)
2. **Envoyer de la telemetrie** (Test du backend) :
   - Topic : `smartstock/ela35/data/esp32-001`
   - Payload JSON : `{"device_id": "esp32-001", "valeur": 42}`
3. **Controler l'actionneur** (Test de l'ESP32) :
   - Topic : `smartstock/ela35/actuator/esp32-001`
   - Payload JSON : `{"state": "on", "mode": "manual", "humidity_threshold_pct": 75.0}`

(Note: Le backend met a jour le topic de l'actionneur automatiquement quand vous utilisez le endpoint REST `POST /actuator/command`).

## Commandes utiles

Backend:

```powershell
Invoke-RestMethod -Uri "http://127.0.0.1:8000/logs/raw?lines=200" -Method Get
Invoke-RestMethod -Uri "http://127.0.0.1:8000/arduino-logs/raw?lines=200" -Method Get
Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/actuator/command" -ContentType "application/json" -Body '{"command":"force_ventilation"}'
```

Frontend:

```powershell
cd frontend
npm run build
npm run preview
```

ESP32:

```powershell
cd esp32
python -m platformio run
```

## Variables d environnement

### InfluxDB

- `INFLUX_URL` par defaut `http://localhost:8181`
- `INFLUX_DATABASE` par defaut `smart_stock`
- `INFLUX_BUCKET` par defaut `smart_stock`
- `INFLUX_ORG` optionnel
- `INFLUX_TOKEN` optionnel
- `INFLUX_TIMEOUT_SECONDS` par defaut `8`

### Regles et prediction

- `ALERT_STOCK_WARNING`
- `ALERT_STOCK_CRITICAL`
- `ALERT_TEMP_WARNING_C`
- `ALERT_TEMP_CRITICAL_C`
- `ALERT_HUMIDITY_LOW_WARNING`
- `ALERT_HUMIDITY_HIGH_WARNING`
- `ALERT_HUMIDITY_LOW_CRITICAL`
- `ALERT_HUMIDITY_HIGH_CRITICAL`
- `ALERT_COMBINATION_TEMP_BOOST_C`
- `ALERT_COOLDOWN_SECONDS`
- `PREDICTIVE_ALERT_DAYS` par defaut `3`

### Notifications et actionneur

- `TELEGRAM_TOKEN`
- `TELEGRAM_CHAT_ID`
- `ACTUATOR_MODE` (`auto` ou `manual`, defaut `auto`)
- `ACTUATOR_HUMIDITY_THRESHOLD_PCT` par defaut `75`

## Payload capteur

Mode legacy retrocompatible:

```json
{
  "valeur": 42
}
```

Mode recommande:

```json
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
```

## Endpoints API & MQTT

### Topics MQTT

- Telemetrie (Publie par ESP32, ecoute par Backend) : `smartstock/ela35/data/+`
- Actionneur (Publie par Backend, ecoute par ESP32) : `smartstock/ela35/actuator/esp32-001`

### Telemetrie (REST)

- `GET /data?limit=120&produit_id=produit-1`
- `GET /products`
- `GET /prediction?produit_id=produit-1`

### Alertes

- `GET /alerts`
- `POST /alerts/{alert_id}/ack`
- `GET /alerts/config`
- `POST /alerts/config`

### Actionneur (REST)

- `GET /actuator/state`
- `POST /actuator/config`
- `POST /actuator/command`
- commandes possibles: `auto`, `on`, `off`, `force_ventilation`

### Observabilite

- `GET /health`
- `GET /logs`
- `GET /logs/raw`
- `POST /arduino-log`
- `GET /arduino-logs`
- `GET /arduino-logs/raw`

### Temps reel

- WebSocket `WS /ws`
- evenements: `telemetry`, `telemetry_batch`, `alert`, `actuator`, `heartbeat`

## Prediction de rupture

Le backend calcule une regression lineaire sur l historique de chaque produit. Quand la rupture estimee est proche de `PREDICTIVE_ALERT_DAYS`, une alerte proactive `rupture_prevue` peut etre generee.

Exemple:

```json
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
```

## Schema InfluxDB 3

Measurement: `stock_data`

Tags:

- `produit_id`
- `device_id` optionnel
- `product`

Fields:

- `distance` float
- `humidite` float
- `etat_relais` int `0/1`
- `angle_servo` int `0/90`
- `valeur` int optionnel
- `temperature_c` float

Time column:

- `time`

## Exemples SQL InfluxDB 3

Derniers points:

```sql
SELECT time, produit_id, product, valeur, humidite, etat_relais, angle_servo
FROM stock_data
ORDER BY time DESC
LIMIT 50;
```

Filtre par produit:

```sql
SELECT time, valeur, distance, humidite
FROM stock_data
WHERE produit_id = 'produit-1'
ORDER BY time DESC
LIMIT 100;
```

## Diagnostic rapide

- Si `GET /health` echoue, verifiez d abord InfluxDB 3 puis le backend.
- Si le frontend ne charge pas, relancez `npm install` puis `npm run dev`.
- Si la simulation Wokwi ne recoit rien, verifiez la connexion internet (broker `broker.hivemq.com`).
- Si vous testez les alertes, regardez aussi `GET /alerts` et `GET /logs/raw`.
