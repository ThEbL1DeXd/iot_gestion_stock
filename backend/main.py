from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Any, Deque, Dict, List, Literal, Optional, Tuple
import calendar
import json
import logging
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, ConfigDict, Field
import numpy as np
from sklearn.linear_model import LinearRegression

try:
    from .services.influx_service import InfluxConfig, InfluxService
    from .services.notifier import NotificationContext, NotificationService
    from .services.rules_engine import (
        CrisisInput,
        RuleThresholds,
        RulesEngine,
        LEVEL_NORMAL,
    )
except ImportError:
    from services.influx_service import InfluxConfig, InfluxService
    from services.notifier import NotificationContext, NotificationService
    from services.rules_engine import (
        CrisisInput,
        RuleThresholds,
        RulesEngine,
        LEVEL_NORMAL,
    )


app = FastAPI(title="Smart Stock API - InfluxDB 3")


def _load_env_file(path: Path) -> int:
    if not path.exists() or not path.is_file():
        return 0

    loaded = 0
    try:
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue

            if line.lower().startswith("export "):
                line = line[7:].strip()

            if "=" not in line:
                continue

            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()

            if not key:
                continue

            if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
                value = value[1:-1]

            if key not in os.environ:
                os.environ[key] = value
                loaded += 1
    except OSError:
        return loaded

    return loaded


def _bootstrap_env() -> None:
    backend_dir = Path(__file__).resolve().parent
    project_root = backend_dir.parent

    candidates = [
        project_root / ".env",
        backend_dir / ".env",
        backend_dir / "services" / ".env",
    ]

    for candidate in candidates:
        _load_env_file(candidate)


_bootstrap_env()

logger = logging.getLogger("smart_stock")
if not logger.handlers:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

LOG_DIR = os.path.join(os.path.dirname(__file__), "logs")
LOG_FILE = os.path.join(LOG_DIR, "iot_events.txt")
ARDUINO_LOG_FILE = os.path.join(LOG_DIR, "arduino_serial.txt")

MIN_REASONABLE_EPOCH_MS = 946684800000  # 2000-01-01T00:00:00Z
MAX_REASONABLE_EPOCH_MS = 4102444800000  # 2100-01-01T00:00:00Z


def _predictive_alert_days() -> int:
    raw = os.getenv("PREDICTIVE_ALERT_DAYS", "3")
    try:
        return max(1, min(30, int(raw)))
    except (TypeError, ValueError):
        return 3


def _utc_now() -> datetime:
    return datetime.utcnow()


def _default_cooldown_seconds() -> int:
    raw = os.getenv("ALERT_COOLDOWN_SECONDS", "300")
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return 300


def _default_actuator_mode() -> str:
    mode = os.getenv("ACTUATOR_MODE", "auto").strip().lower()
    return mode if mode in {"auto", "manual"} else "auto"


def _default_actuator_threshold() -> float:
    raw = os.getenv("ACTUATOR_HUMIDITY_THRESHOLD_PCT", "75")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = 75.0
    return max(0.0, min(100.0, value))


rules_engine = RulesEngine()
notification_service = NotificationService(default_cooldown_seconds=_default_cooldown_seconds())
influx_service = InfluxService(InfluxConfig.from_env())

app.state.thresholds = RuleThresholds.from_env()
app.state.cooldown_seconds = notification_service.default_cooldown_seconds
app.state.cooldowns: Dict[str, datetime] = {}
app.state.influx = influx_service
app.state.telemetry_cache: Deque[Dict[str, Any]] = deque(maxlen=4000)
app.state.product_labels: Dict[str, str] = {}
app.state.alerts: List[Dict[str, Any]] = []
app.state.next_row_id = 1
app.state.next_alert_id = 1
app.state.actuator = {
    "mode": _default_actuator_mode(),
    "state": "off",
    "humidity_threshold_pct": _default_actuator_threshold(),
    "updated_at": _utc_now().strftime("%Y-%m-%d %H:%M:%S"),
    "reason": "startup",
}


# CORS for React frontend.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: str):
        stale_connections: List[WebSocket] = []
        for connection in list(self.active_connections):
            try:
                await connection.send_text(message)
            except Exception:
                stale_connections.append(connection)

        for connection in stale_connections:
            self.disconnect(connection)


manager = ConnectionManager()


class SensorReading(BaseModel):
    produit_id: str = Field(default="produit-1", min_length=1, max_length=60)
    product: str = Field(default="Produit principal", min_length=1, max_length=120)
    valeur: Optional[int] = Field(default=None, ge=0, le=100)
    distance_cm: Optional[float] = Field(default=None, ge=0, le=600)


class TelemetryCreate(BaseModel):
    valeur: Optional[int] = Field(default=None, ge=0, le=100)
    distance_cm: Optional[float] = Field(default=None, ge=0, le=600)
    product: str = Field(default="Produit principal", min_length=1, max_length=120)
    produit_id: Optional[str] = Field(default=None, min_length=1, max_length=60)
    device_id: str = Field(default="esp32-default", min_length=1, max_length=80)
    temperature_c: Optional[float] = Field(default=None, ge=-40, le=125)
    humidity_pct: Optional[float] = Field(default=None, ge=0, le=100)
    timestamp_ms: Optional[int] = Field(default=None, ge=0)
    sensors: Optional[List[SensorReading]] = None

    model_config = ConfigDict(extra="ignore")


class AlertResponse(BaseModel):
    id: int
    alert_type: str
    level: str
    product: str
    produit_id: str
    valeur: int
    temperature_c: Optional[float] = None
    humidity_pct: Optional[float] = None
    reasons: str
    recommendation: str
    risk_score: int
    cooldown_until: Optional[str] = None
    sent_channels: List[str] = Field(default_factory=list)
    notification_suppressed: bool
    acknowledged: bool
    created_at: str


class AlertAcknowledgeResponse(BaseModel):
    status: str
    alert_id: int
    acknowledged: bool


class AlertConfigUpdate(BaseModel):
    stock_warning: Optional[int] = Field(default=None, ge=0, le=100)
    stock_critical: Optional[int] = Field(default=None, ge=0, le=100)
    temp_warning_c: Optional[float] = Field(default=None, ge=-40, le=125)
    temp_critical_c: Optional[float] = Field(default=None, ge=-40, le=125)
    humidity_low_warning: Optional[float] = Field(default=None, ge=0, le=100)
    humidity_high_warning: Optional[float] = Field(default=None, ge=0, le=100)
    humidity_low_critical: Optional[float] = Field(default=None, ge=0, le=100)
    humidity_high_critical: Optional[float] = Field(default=None, ge=0, le=100)
    combination_temp_boost_c: Optional[float] = Field(default=None, ge=-40, le=125)
    cooldown_seconds: Optional[int] = Field(default=None, ge=0, le=86400)


class ActuatorConfigUpdate(BaseModel):
    humidity_threshold_pct: Optional[float] = Field(default=None, ge=0, le=100)
    mode: Optional[Literal["auto", "manual"]] = None


class ActuatorCommand(BaseModel):
    command: Literal["auto", "on", "off", "force_ventilation"]


class ArduinoLogCreate(BaseModel):
    event: str
    message: str = ""
    distance: Optional[float] = None
    valeur: Optional[int] = None
    http_code: Optional[int] = None


def append_log(event: str, payload: Any):
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        entry = {
            "timestamp": _utc_now().strftime("%Y-%m-%d %H:%M:%S"),
            "event": event,
            "payload": payload,
        }
        with open(LOG_FILE, "a", encoding="utf-8") as log_file:
            log_file.write(json.dumps(entry) + "\n")
    except Exception:
        pass


def append_arduino_log_line(
    event: str,
    message: str = "",
    distance: Optional[float] = None,
    valeur: Optional[int] = None,
    http_code: Optional[int] = None,
):
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        parts = [
            f"[{_utc_now().strftime('%Y-%m-%d %H:%M:%S')} UTC]",
            f"event={event}",
        ]
        if message:
            parts.append(f"msg={message}")
        if distance is not None:
            parts.append(f"distance={distance:.2f}")
        if valeur is not None:
            parts.append(f"valeur={valeur}")
        if http_code is not None:
            parts.append(f"http={http_code}")

        with open(ARDUINO_LOG_FILE, "a", encoding="utf-8") as log_file:
            log_file.write(" | ".join(parts) + "\n")
    except Exception:
        pass


def _clamp_stock(value: int) -> int:
    return max(0, min(100, int(value)))


def _distance_from_stock(stock_percent: int) -> float:
    return round(2 + ((100 - _clamp_stock(stock_percent)) / 100) * 398, 1)


def _stock_from_distance(distance_cm: float) -> int:
    ratio = (float(distance_cm) - 2.0) / 398.0
    mapped = int(round(100 - (ratio * 100)))
    return _clamp_stock(mapped)


def _is_reasonable_epoch_ms(timestamp_ms: Optional[int]) -> bool:
    if timestamp_ms is None:
        return False
    try:
        value = int(timestamp_ms)
    except (TypeError, ValueError):
        return False
    return MIN_REASONABLE_EPOCH_MS <= value <= MAX_REASONABLE_EPOCH_MS


def _datetime_to_epoch_ms(value: datetime) -> int:
    # Use UTC tuple conversion to avoid platform-specific issues for early dates on Windows.
    seconds = calendar.timegm(value.utctimetuple())
    return int(seconds * 1000 + (value.microsecond // 1000))


def _datetime_from_timestamp_ms(timestamp_ms: Optional[int]) -> datetime:
    if not _is_reasonable_epoch_ms(timestamp_ms):
        return _utc_now()
    try:
        return datetime.utcfromtimestamp(float(int(timestamp_ms)) / 1000.0)
    except (TypeError, ValueError, OSError):
        return _utc_now()


def _serialize_thresholds(thresholds: RuleThresholds, cooldown_seconds: int) -> dict:
    return {
        **thresholds.to_dict(),
        "cooldown_seconds": cooldown_seconds,
    }


def _normalize_sensor_rows(payload: TelemetryCreate) -> List[Dict[str, Any]]:
    if payload.sensors:
        source = payload.sensors
    else:
        source = [
            SensorReading(
                produit_id=(payload.produit_id or "produit-1"),
                product=payload.product,
                valeur=payload.valeur,
                distance_cm=payload.distance_cm,
            )
        ]

    normalized: List[Dict[str, Any]] = []
    for index, sensor in enumerate(source, start=1):
        produit_id = (sensor.produit_id or f"produit-{index}").strip()
        product = (sensor.product or f"Produit {index}").strip()

        valeur = sensor.valeur
        if valeur is None and sensor.distance_cm is not None:
            valeur = _stock_from_distance(sensor.distance_cm)
        if valeur is None and payload.valeur is not None:
            valeur = payload.valeur
        if valeur is None:
            valeur = 0
        valeur = _clamp_stock(valeur)

        distance_cm = sensor.distance_cm
        if distance_cm is None:
            distance_cm = _distance_from_stock(valeur)

        normalized.append(
            {
                "produit_id": produit_id,
                "product": product,
                "valeur": valeur,
                "distance_cm": round(float(distance_cm), 1),
            }
        )

    return normalized


def _prediction_from_history(history: List[Dict[str, Any]]):
    if len(history) < 3:
        return "N/A"

    try:
        y = np.array([int(row["valeur"]) for row in history[-10:]], dtype=float)
        x = np.array(range(len(y))).reshape(-1, 1)
        model = LinearRegression()
        model.fit(x, y)
        next_x = np.array([[len(y)]])
        pred_val = model.predict(next_x)[0]
        return _clamp_stock(int(pred_val))
    except Exception as exc:
        append_log("prediction_error", {"error": str(exc)})
        return "N/A"


def _severity_rank(level: str) -> int:
    if level == "Critique":
        return 2
    if level == "Alerte":
        return 1
    return 0


def _max_severity_level(level_a: str, level_b: str) -> str:
    return level_a if _severity_rank(level_a) >= _severity_rank(level_b) else level_b


def _timestamp_ms_from_row(row: Dict[str, Any]) -> Optional[int]:
    raw_ms = row.get("timestamp_ms")
    if _is_reasonable_epoch_ms(raw_ms):
        return int(raw_ms)

    raw_ts = row.get("timestamp")
    if not raw_ts:
        return None

    text = str(raw_ts).strip()
    if not text:
        return None

    if text.endswith("Z"):
        text = text[:-1] + "+00:00"

    parsed: Optional[datetime] = None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        try:
            parsed = datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None

    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)

    return _datetime_to_epoch_ms(parsed)


def _depletion_forecast_from_history(
    history: List[Dict[str, Any]],
    produit_id: str,
    product: str,
) -> Dict[str, Any]:
    risk_window_days = _predictive_alert_days()
    baseline = {
        "status": "insufficient_data",
        "produit_id": produit_id,
        "product": product,
        "days_to_empty": None,
        "estimated_empty_at": None,
        "consumption_rate_pct_per_day": None,
        "model_r2": None,
        "risk_level": LEVEL_NORMAL,
        "risk_window_days": risk_window_days,
        "message": "Donnees insuffisantes pour estimer la rupture",
    }

    samples: List[Tuple[int, float]] = []
    for row in history[-120:]:
        ts_ms = _timestamp_ms_from_row(row)
        if ts_ms is None:
            continue

        try:
            stock_percent = float(row.get("valeur"))
        except (TypeError, ValueError):
            continue

        if not np.isfinite(stock_percent):
            continue

        samples.append((ts_ms, max(0.0, min(100.0, stock_percent))))

    if len(samples) < 4:
        return baseline

    dedup: Dict[int, float] = {}
    for ts_ms, stock_percent in samples:
        dedup[ts_ms] = stock_percent

    ordered = sorted(dedup.items(), key=lambda item: item[0])
    if len(ordered) < 4:
        return baseline

    span_days = (ordered[-1][0] - ordered[0][0]) / 86400000.0
    if span_days <= 0.05:
        result = dict(baseline)
        result["message"] = "Historique trop court pour une prediction fiable"
        return result

    x_values = np.array([(ts_ms - ordered[0][0]) / 86400000.0 for ts_ms, _ in ordered], dtype=float).reshape(-1, 1)
    y_values = np.array([stock for _, stock in ordered], dtype=float)

    model = LinearRegression()
    model.fit(x_values, y_values)

    slope = float(model.coef_[0])
    r2 = float(model.score(x_values, y_values)) if len(ordered) >= 4 else 0.0
    current_stock = float(y_values[-1])

    if slope >= -0.05:
        return {
            **baseline,
            "status": "stable_or_refilling",
            "consumption_rate_pct_per_day": round(abs(min(slope, 0.0)), 3),
            "model_r2": round(r2, 3),
            "message": "Consommation stable: aucune rupture imminente estimee",
        }

    days_to_empty = current_stock / abs(slope) if current_stock > 0 else 0.0
    if not np.isfinite(days_to_empty):
        return baseline

    days_to_empty = max(0.0, float(days_to_empty))
    empty_at_ms = ordered[-1][0] + int(days_to_empty * 86400000)
    empty_at = datetime.utcfromtimestamp(empty_at_ms / 1000.0).strftime("%Y-%m-%d %H:%M:%S")

    risk_level = LEVEL_NORMAL
    if days_to_empty <= 1.0:
        risk_level = "Critique"
    elif days_to_empty <= risk_window_days:
        risk_level = "Alerte"

    return {
        "status": "ok",
        "produit_id": produit_id,
        "product": product,
        "days_to_empty": round(days_to_empty, 2),
        "estimated_empty_at": empty_at,
        "consumption_rate_pct_per_day": round(abs(slope), 3),
        "model_r2": round(r2, 3),
        "risk_level": risk_level,
        "risk_window_days": risk_window_days,
        "message": f"Au rythme actuel, rupture estimee dans {days_to_empty:.1f} jours",
    }


def _next_row_id() -> int:
    current = app.state.next_row_id
    app.state.next_row_id += 1
    return current


def _next_alert_id() -> int:
    current = app.state.next_alert_id
    app.state.next_alert_id += 1
    return current


def _actuator_snapshot() -> Dict[str, Any]:
    state = app.state.actuator
    relay_state = 1 if state["state"] == "on" else 0
    servo_angle = 90 if relay_state else 0
    return {
        "mode": state["mode"],
        "state": state["state"],
        "etat_relais": relay_state,
        "angle_servo": servo_angle,
        "humidity_threshold_pct": float(state["humidity_threshold_pct"]),
        "updated_at": state["updated_at"],
        "reason": state.get("reason", ""),
    }


def _update_actuator_state(state: str, mode: Optional[str] = None, reason: str = "") -> bool:
    state = state.lower()
    if state not in {"on", "off"}:
        return False

    changed = False
    if app.state.actuator["state"] != state:
        app.state.actuator["state"] = state
        changed = True

    if mode and mode in {"auto", "manual"} and app.state.actuator["mode"] != mode:
        app.state.actuator["mode"] = mode
        changed = True

    if reason:
        app.state.actuator["reason"] = reason

    if changed:
        app.state.actuator["updated_at"] = _utc_now().strftime("%Y-%m-%d %H:%M:%S")

    return changed


def _latest_humidity() -> Optional[float]:
    if not app.state.telemetry_cache:
        return None
    latest = app.state.telemetry_cache[-1]
    humidity = latest.get("humidity_pct")
    if humidity is None:
        return None
    return float(humidity)


def _apply_auto_actuator(humidity_pct: Optional[float]) -> bool:
    if app.state.actuator["mode"] != "auto" or humidity_pct is None:
        return False

    threshold = float(app.state.actuator["humidity_threshold_pct"])
    desired_state = "on" if float(humidity_pct) >= threshold else "off"
    previous_state = app.state.actuator["state"]
    reason = (
        f"auto_humidity_{float(humidity_pct):.1f}_gte_{threshold:.1f}"
        if desired_state == "on"
        else f"auto_humidity_{float(humidity_pct):.1f}_lt_{threshold:.1f}"
    )
    changed = _update_actuator_state(desired_state, mode="auto", reason=reason)

    if changed and previous_state != "on" and desired_state == "on":
        rounded_humidity = int(round(float(humidity_pct)))
        notification_service.send_telegram_message(
            f"⚠️ Alerte Humidité : {rounded_humidity}% ! Activation de la ventilation"
        )

    return changed


def _cache_history(
    limit: int = 50,
    produit_id: Optional[str] = None,
    device_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    for row in app.state.telemetry_cache:
        if produit_id and row.get("produit_id") != produit_id:
            continue
        if device_id and row.get("device_id") != device_id:
            continue
        records.append(dict(row))

    limit = max(1, min(limit, 500))
    if len(records) > limit:
        records = records[-limit:]

    return records


def _can_notify_now(fingerprint: str, now: datetime) -> Tuple[bool, datetime]:
    cooldown_until = app.state.cooldowns.get(fingerprint)
    if cooldown_until and cooldown_until > now:
        return False, cooldown_until

    next_until = now + timedelta(seconds=app.state.cooldown_seconds)
    app.state.cooldowns[fingerprint] = next_until
    return True, next_until


def _serialize_alert(alert: Dict[str, Any]) -> Dict[str, Any]:
    channels = [value for value in (alert.get("sent_channels") or "").split(",") if value]
    cooldown_until = alert.get("cooldown_until")
    created_at = alert.get("created_at")

    return {
        "id": alert["id"],
        "alert_type": alert["alert_type"],
        "level": alert["level"],
        "product": alert["product"],
        "produit_id": alert.get("produit_id", "produit-1"),
        "valeur": alert["valeur"],
        "temperature_c": alert.get("temperature_c"),
        "humidity_pct": alert.get("humidity_pct"),
        "reasons": alert["reasons"],
        "recommendation": alert["recommendation"],
        "risk_score": alert["risk_score"],
        "cooldown_until": cooldown_until.strftime("%Y-%m-%d %H:%M:%S")
        if isinstance(cooldown_until, datetime)
        else cooldown_until,
        "sent_channels": channels,
        "notification_suppressed": bool(alert.get("notification_suppressed", False)),
        "acknowledged": bool(alert.get("acknowledged", False)),
        "created_at": created_at.strftime("%Y-%m-%d %H:%M:%S")
        if isinstance(created_at, datetime)
        else str(created_at),
    }


def _add_alert(alert: Dict[str, Any]):
    app.state.alerts.insert(0, alert)
    if len(app.state.alerts) > 1500:
        del app.state.alerts[1500:]


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    append_log(
        "validation_error",
        {"path": str(request.url.path), "errors": exc.errors()},
    )
    return JSONResponse(
        status_code=422,
        content={
            "status": "error",
            "message": "Validation des donnees invalide",
            "errors": exc.errors(),
        },
    )


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.exception("Unhandled API error on %s", request.url.path)
    append_log(
        "internal_error",
        {"path": str(request.url.path), "error": str(exc)},
    )
    return JSONResponse(
        status_code=500,
        content={
            "status": "error",
            "message": "Erreur interne du serveur",
        },
    )


@app.get("/health")
def health_check():
    influx_ok, influx_message = app.state.influx.is_available()
    return {
        "status": "ok",
        "database_backend": "influxdb3",
        "influxdb3": "ok" if influx_ok else "down",
        "influx_message": influx_message,
        "actuator": _actuator_snapshot(),
        "time": _utc_now().strftime("%Y-%m-%d %H:%M:%S"),
    }


@app.post("/data")
async def receive_data(payload: TelemetryCreate):
    timestamp = _datetime_from_timestamp_ms(payload.timestamp_ms)
    timestamp_ms = int(payload.timestamp_ms) if _is_reasonable_epoch_ms(payload.timestamp_ms) else _datetime_to_epoch_ms(timestamp)

    sensor_rows = _normalize_sensor_rows(payload)

    append_log(
        "incoming_data",
        {
            "device_id": payload.device_id,
            "sensors_count": len(sensor_rows),
            "temperature_c": payload.temperature_c,
            "humidity_pct": payload.humidity_pct,
            "timestamp_ms": timestamp_ms,
        },
    )

    actuator_changed = _apply_auto_actuator(payload.humidity_pct)

    ws_events: List[Dict[str, Any]] = []
    created_alerts: List[Dict[str, Any]] = []
    rows_for_influx: List[Dict[str, Any]] = []

    for sensor in sensor_rows:
        relay_state = 1 if app.state.actuator["state"] == "on" else 0
        servo_angle = 90 if relay_state else 0
        row = {
            "id": _next_row_id(),
            "date": timestamp.strftime("%H:%M:%S"),
            "timestamp": timestamp.strftime("%Y-%m-%d %H:%M:%S"),
            "timestamp_ms": timestamp_ms,
            "device_id": payload.device_id.strip() or "esp32-default",
            "produit_id": sensor["produit_id"],
            "product": sensor["product"],
            "valeur": sensor["valeur"],
            "distance_cm": sensor["distance_cm"],
            "temperature_c": payload.temperature_c,
            "humidity_pct": payload.humidity_pct,
            "etat_relais": relay_state,
            "angle_servo": servo_angle,
            "actuator_state": app.state.actuator["state"],
        }

        app.state.telemetry_cache.append(row)
        app.state.product_labels[row["produit_id"]] = row["product"]
        rows_for_influx.append(row)

        history = _cache_history(limit=80, produit_id=row["produit_id"])
        prediction = _prediction_from_history(history)
        depletion_forecast = _depletion_forecast_from_history(
            history=history,
            produit_id=row["produit_id"],
            product=row["product"],
        )

        thresholds: RuleThresholds = app.state.thresholds
        crisis = rules_engine.evaluate(
            CrisisInput(
                stock_percent=row["valeur"],
                temperature_c=row["temperature_c"],
                humidity_pct=row["humidity_pct"],
            ),
            thresholds,
        )

        forecast_level = "N/A"
        if prediction != "N/A":
            forecast = rules_engine.evaluate(
                CrisisInput(
                    stock_percent=int(prediction),
                    temperature_c=row["temperature_c"],
                    humidity_pct=row["humidity_pct"],
                ),
                thresholds,
            )
            forecast_level = forecast.level

        final_level = crisis.level
        final_alert_type = crisis.alert_type
        final_reasons = list(crisis.reasons)
        final_recommendations = list(crisis.recommendations)
        final_risk_score = int(crisis.risk_score)

        if depletion_forecast.get("status") == "ok":
            depletion_risk_level = str(depletion_forecast.get("risk_level", LEVEL_NORMAL))
            if depletion_risk_level != LEVEL_NORMAL:
                final_level = _max_severity_level(final_level, depletion_risk_level)
                final_alert_type = "rupture_prevue"
                days_text = float(depletion_forecast.get("days_to_empty") or 0.0)
                eta_text = str(depletion_forecast.get("estimated_empty_at") or "--")
                final_reasons.append(f"Rupture estimee dans {days_text:.1f} jours (ETA {eta_text})")
                final_recommendations.append("Planifier un reapprovisionnement preventif avant la date ETA")
                final_risk_score = max(final_risk_score, 90 if days_text <= 1.0 else 70)

        final_reasons = list(dict.fromkeys(final_reasons))
        final_recommendations = list(dict.fromkeys(final_recommendations))

        alert_payload = None
        sent_channels: List[str] = []

        if final_level != LEVEL_NORMAL:
            now = _utc_now()
            fingerprint = notification_service.build_fingerprint(
                level=final_level,
                alert_type=final_alert_type,
                product=row["product"],
            )
            force_notify_high_risk = final_level == "Critique" and final_risk_score >= 95

            if force_notify_high_risk:
                can_notify = True
                cooldown_until = now + timedelta(seconds=app.state.cooldown_seconds)
                app.state.cooldowns[fingerprint] = cooldown_until
            else:
                can_notify, cooldown_until = _can_notify_now(fingerprint, now)

            alert_record = {
                "id": _next_alert_id(),
                "alert_type": final_alert_type,
                "level": final_level,
                "product": row["product"],
                "produit_id": row["produit_id"],
                "valeur": row["valeur"],
                "temperature_c": row["temperature_c"],
                "humidity_pct": row["humidity_pct"],
                "reasons": "; ".join(final_reasons),
                "recommendation": "; ".join(final_recommendations),
                "risk_score": final_risk_score,
                "fingerprint": fingerprint,
                "cooldown_until": cooldown_until,
                "sent_channels": "",
                "notification_suppressed": not can_notify,
                "acknowledged": False,
                "created_at": now,
            }

            if can_notify:
                sent_channels = notification_service.notify(
                    NotificationContext(
                        alert_id=alert_record["id"],
                        level=alert_record["level"],
                        alert_type=alert_record["alert_type"],
                        product=alert_record["product"],
                        valeur=alert_record["valeur"],
                        temperature_c=alert_record["temperature_c"],
                        humidity_pct=alert_record["humidity_pct"],
                        reasons=alert_record["reasons"],
                        recommendation=alert_record["recommendation"],
                        risk_score=alert_record["risk_score"],
                        created_at=alert_record["created_at"],
                    )
                )

            alert_record["sent_channels"] = ",".join(sent_channels)
            _add_alert(alert_record)
            alert_payload = _serialize_alert(alert_record)
            created_alerts.append(alert_payload)

        ws_message = {
            "event": "telemetry",
            "id": row["id"],
            "valeur": row["valeur"],
            "date": row["date"],
            "timestamp": row["timestamp"],
            "produit_id": row["produit_id"],
            "product": row["product"],
            "device_id": row["device_id"],
            "distance_cm": row["distance_cm"],
            "temperature_c": row["temperature_c"],
            "humidity_pct": row["humidity_pct"],
            "etat_relais": row["etat_relais"],
            "angle_servo": row["angle_servo"],
            "prediction": prediction,
            "forecast_level": forecast_level,
            "depletion_forecast": depletion_forecast,
            "alerte": final_level != LEVEL_NORMAL,
            "level": final_level,
            "alert_type": final_alert_type,
            "reasons": final_reasons,
            "recommendations": final_recommendations,
            "recommendation": "; ".join(final_recommendations),
            "risk_score": final_risk_score,
            "alert_id": alert_payload["id"] if alert_payload else None,
            "notification_suppressed": alert_payload["notification_suppressed"] if alert_payload else False,
            "sent_channels": alert_payload["sent_channels"] if alert_payload else [],
            "actuator": _actuator_snapshot(),
        }

        ws_events.append(ws_message)

    influx_ok, influx_message = app.state.influx.write_telemetry(rows_for_influx)
    if not influx_ok:
        append_log("influx_write_error", {"error": influx_message})

    if actuator_changed:
        await manager.broadcast(
            json.dumps(
                {
                    "event": "actuator",
                    "actuator": _actuator_snapshot(),
                }
            )
        )

    for event in ws_events:
        await manager.broadcast(json.dumps(event))

    for alert in created_alerts:
        await manager.broadcast(
            json.dumps(
                {
                    "event": "alert",
                    "alert": alert,
                }
            )
        )

    await manager.broadcast(
        json.dumps(
            {
                "event": "telemetry_batch",
                "device_id": payload.device_id,
                "temperature_c": payload.temperature_c,
                "humidity_pct": payload.humidity_pct,
                "actuator": _actuator_snapshot(),
                "sensors": ws_events,
            }
        )
    )

    response_data: Dict[str, Any]
    if len(ws_events) == 1:
        response_data = ws_events[0]
    else:
        response_data = {
            "event": "telemetry_batch",
            "sensors": ws_events,
            "device_id": payload.device_id,
            "temperature_c": payload.temperature_c,
            "humidity_pct": payload.humidity_pct,
            "actuator": _actuator_snapshot(),
        }

    return {
        "status": "success",
        "storage": "influxdb3" if influx_ok else "memory_fallback",
        "influx_message": influx_message,
        "data": response_data,
        "alerts": created_alerts,
        "actuator": _actuator_snapshot(),
    }


@app.get("/data")
def get_history(
    limit: int = 50,
    produit_id: Optional[str] = None,
    device_id: Optional[str] = None,
):
    limit = max(1, min(limit, 500))

    influx_ok, rows, influx_error = app.state.influx.query_recent(
        limit=limit,
        produit_id=produit_id,
        device_id=device_id,
    )

    if not influx_ok or not rows:
        if not influx_ok:
            append_log("influx_query_error", {"error": influx_error})
        rows = _cache_history(limit=limit, produit_id=produit_id, device_id=device_id)

    normalized_rows: List[Dict[str, Any]] = []
    for index, row in enumerate(rows, start=1):
        relay_raw = row.get("etat_relais")
        relay_on = False
        if relay_raw is not None:
            relay_on = str(relay_raw).strip().lower() in {"1", "true", "on", "yes"}
        elif str(row.get("actuator_state", "off")).lower() == "on":
            relay_on = True

        angle_raw = row.get("angle_servo")
        try:
            angle_servo = int(float(angle_raw)) if angle_raw is not None else (90 if relay_on else 0)
        except (TypeError, ValueError):
            angle_servo = 90 if relay_on else 0
        angle_servo = max(0, min(180, angle_servo))

        normalized_rows.append(
            {
                "id": row.get("id") or index,
                "valeur": int(row.get("valeur", 0)),
                "date": row.get("date") or _datetime_from_timestamp_ms(row.get("timestamp_ms")).strftime("%H:%M:%S"),
                "timestamp": row.get("timestamp"),
                "timestamp_ms": row.get("timestamp_ms"),
                "product": row.get("product", "Produit principal"),
                "produit_id": row.get("produit_id", "produit-1"),
                "device_id": row.get("device_id", "esp32-default"),
                "distance_cm": row.get("distance_cm"),
                "temperature_c": row.get("temperature_c"),
                "humidity_pct": row.get("humidity_pct"),
                "actuator_state": "on" if relay_on else "off",
                "etat_relais": 1 if relay_on else 0,
                "angle_servo": angle_servo,
            }
        )

    return normalized_rows


@app.get("/products")
def get_products():
    ok, products, error = app.state.influx.query_products()
    if ok and products:
        for item in products:
            app.state.product_labels[item["produit_id"]] = item["product"]
        return products

    if not ok:
        append_log("influx_products_error", {"error": error})

    return [
        {"produit_id": produit_id, "product": product}
        for produit_id, product in sorted(app.state.product_labels.items(), key=lambda item: item[0])
    ]


@app.get("/prediction")
def get_prediction(
    produit_id: str,
    device_id: Optional[str] = None,
    limit: int = 120,
):
    limit = max(10, min(limit, 500))

    influx_ok, rows, influx_error = app.state.influx.query_recent(
        limit=limit,
        produit_id=produit_id,
        device_id=device_id,
    )

    if not influx_ok or not rows:
        if not influx_ok:
            append_log("prediction_influx_query_error", {"error": influx_error})
        rows = _cache_history(limit=limit, produit_id=produit_id, device_id=device_id)

    product_label = app.state.product_labels.get(produit_id, produit_id)
    if rows:
        latest = rows[-1]
        product_label = str(latest.get("product") or product_label)

    forecast = _depletion_forecast_from_history(
        history=rows,
        produit_id=produit_id,
        product=product_label,
    )
    forecast["source"] = "influxdb3" if influx_ok and rows else "memory_cache"
    return forecast


@app.get("/alerts", response_model=List[AlertResponse])
def get_alerts(limit: int = 100):
    limit = max(1, min(limit, 500))
    records = app.state.alerts[:limit]
    return [_serialize_alert(record) for record in records]


@app.post("/alerts/{alert_id}/ack", response_model=AlertAcknowledgeResponse)
def acknowledge_alert(alert_id: int):
    for record in app.state.alerts:
        if record["id"] == alert_id:
            record["acknowledged"] = True
            return {
                "status": "success",
                "alert_id": alert_id,
                "acknowledged": True,
            }

    raise HTTPException(status_code=404, detail="Alert introuvable")


@app.get("/alerts/config")
def get_alert_config():
    return _serialize_thresholds(app.state.thresholds, app.state.cooldown_seconds)


@app.post("/alerts/config")
def update_alert_config(payload: AlertConfigUpdate):
    current: RuleThresholds = app.state.thresholds

    updated = RuleThresholds(
        stock_warning=payload.stock_warning if payload.stock_warning is not None else current.stock_warning,
        stock_critical=payload.stock_critical if payload.stock_critical is not None else current.stock_critical,
        temp_warning_c=payload.temp_warning_c if payload.temp_warning_c is not None else current.temp_warning_c,
        temp_critical_c=payload.temp_critical_c if payload.temp_critical_c is not None else current.temp_critical_c,
        humidity_low_warning=payload.humidity_low_warning
        if payload.humidity_low_warning is not None
        else current.humidity_low_warning,
        humidity_high_warning=payload.humidity_high_warning
        if payload.humidity_high_warning is not None
        else current.humidity_high_warning,
        humidity_low_critical=payload.humidity_low_critical
        if payload.humidity_low_critical is not None
        else current.humidity_low_critical,
        humidity_high_critical=payload.humidity_high_critical
        if payload.humidity_high_critical is not None
        else current.humidity_high_critical,
        combination_temp_boost_c=payload.combination_temp_boost_c
        if payload.combination_temp_boost_c is not None
        else current.combination_temp_boost_c,
    )

    if updated.stock_critical > updated.stock_warning:
        raise HTTPException(
            status_code=400,
            detail="stock_critical doit etre <= stock_warning",
        )
    if updated.temp_critical_c < updated.temp_warning_c:
        raise HTTPException(
            status_code=400,
            detail="temp_critical_c doit etre >= temp_warning_c",
        )
    if updated.humidity_low_critical > updated.humidity_low_warning:
        raise HTTPException(
            status_code=400,
            detail="humidity_low_critical doit etre <= humidity_low_warning",
        )
    if updated.humidity_high_critical < updated.humidity_high_warning:
        raise HTTPException(
            status_code=400,
            detail="humidity_high_critical doit etre >= humidity_high_warning",
        )

    app.state.thresholds = updated

    if payload.cooldown_seconds is not None:
        app.state.cooldown_seconds = payload.cooldown_seconds
        notification_service.default_cooldown_seconds = payload.cooldown_seconds

    current_config = _serialize_thresholds(app.state.thresholds, app.state.cooldown_seconds)
    append_log("alert_config_updated", current_config)
    return current_config


@app.get("/actuator/state")
def get_actuator_state():
    return _actuator_snapshot()


@app.post("/actuator/config")
async def update_actuator_config(payload: ActuatorConfigUpdate):
    changed = False

    if payload.humidity_threshold_pct is not None:
        threshold = max(0.0, min(100.0, float(payload.humidity_threshold_pct)))
        if float(app.state.actuator["humidity_threshold_pct"]) != threshold:
            app.state.actuator["humidity_threshold_pct"] = threshold
            changed = True

    if payload.mode is not None and payload.mode in {"auto", "manual"}:
        if app.state.actuator["mode"] != payload.mode:
            app.state.actuator["mode"] = payload.mode
            changed = True

    if app.state.actuator["mode"] == "auto":
        humidity = _latest_humidity()
        changed = _apply_auto_actuator(humidity) or changed

    if changed:
        app.state.actuator["updated_at"] = _utc_now().strftime("%Y-%m-%d %H:%M:%S")
        app.state.actuator["reason"] = "config_update"
        await manager.broadcast(
            json.dumps(
                {
                    "event": "actuator",
                    "actuator": _actuator_snapshot(),
                }
            )
        )

    return _actuator_snapshot()


@app.post("/actuator/command")
async def actuator_command(payload: ActuatorCommand):
    command = payload.command.lower()
    changed = False

    if command == "auto":
        if app.state.actuator["mode"] != "auto":
            app.state.actuator["mode"] = "auto"
            changed = True
        changed = _apply_auto_actuator(_latest_humidity()) or changed
        if changed:
            app.state.actuator["reason"] = "manual_switch_to_auto"
    elif command == "force_ventilation":
        changed = _update_actuator_state("on", mode="manual", reason="force_ventilation") or changed
    elif command in {"on", "off"}:
        changed = _update_actuator_state(command, mode="manual", reason="manual_command") or changed

    if changed:
        await manager.broadcast(
            json.dumps(
                {
                    "event": "actuator",
                    "actuator": _actuator_snapshot(),
                }
            )
        )

    return _actuator_snapshot()


@app.get("/logs")
def get_logs(lines: int = 100):
    lines = max(1, min(lines, 1000))
    if not os.path.exists(LOG_FILE):
        return {"log_file": LOG_FILE, "lines": []}

    with open(LOG_FILE, "r", encoding="utf-8") as log_file:
        content = log_file.readlines()

    return {
        "log_file": LOG_FILE,
        "lines": [line.rstrip("\n") for line in content[-lines:]],
    }


@app.get("/logs/raw", response_class=PlainTextResponse)
def get_raw_logs(lines: int = 200):
    lines = max(1, min(lines, 5000))
    if not os.path.exists(LOG_FILE):
        return ""

    with open(LOG_FILE, "r", encoding="utf-8") as log_file:
        content = log_file.readlines()

    return "".join(content[-lines:])


@app.post("/arduino-log")
def receive_arduino_log(log_data: ArduinoLogCreate):
    append_arduino_log_line(
        event=log_data.event,
        message=log_data.message,
        distance=log_data.distance,
        valeur=log_data.valeur,
        http_code=log_data.http_code,
    )
    return {"status": "logged"}


@app.get("/arduino-logs")
def get_arduino_logs(lines: int = 200):
    lines = max(1, min(lines, 5000))
    if not os.path.exists(ARDUINO_LOG_FILE):
        return {"log_file": ARDUINO_LOG_FILE, "lines": []}

    with open(ARDUINO_LOG_FILE, "r", encoding="utf-8") as log_file:
        content = log_file.readlines()

    return {
        "log_file": ARDUINO_LOG_FILE,
        "lines": [line.rstrip("\n") for line in content[-lines:]],
    }


@app.get("/arduino-logs/raw", response_class=PlainTextResponse)
def get_arduino_raw_logs(lines: int = 200):
    lines = max(1, min(lines, 5000))
    if not os.path.exists(ARDUINO_LOG_FILE):
        return ""

    with open(ARDUINO_LOG_FILE, "r", encoding="utf-8") as log_file:
        content = log_file.readlines()

    return "".join(content[-lines:])


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            message = await websocket.receive_text()
            if message.lower() == "ping":
                await websocket.send_text(
                    json.dumps(
                        {
                            "event": "heartbeat",
                            "timestamp": _utc_now().strftime("%Y-%m-%d %H:%M:%S"),
                        }
                    )
                )
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception as exc:
        append_log("ws_error", {"error": str(exc)})
        manager.disconnect(websocket)
