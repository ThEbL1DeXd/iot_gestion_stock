from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from io import StringIO
from typing import Any, Dict, List, Optional, Tuple
import csv
import json
import logging
import os

import requests

logger = logging.getLogger("smart_stock.influx")


@dataclass
class InfluxConfig:
    base_url: str
    database: str
    bucket: str
    org: str
    token: str
    timeout_seconds: float

    @classmethod
    def from_env(cls) -> "InfluxConfig":
        database = os.getenv("INFLUX_DATABASE", "smart_stock")
        return cls(
            base_url=os.getenv("INFLUX_URL", "http://localhost:8181"),
            database=database,
            bucket=os.getenv("INFLUX_BUCKET", database),
            org=os.getenv("INFLUX_ORG", ""),
            token=os.getenv("INFLUX_TOKEN", ""),
            timeout_seconds=max(2.0, float(os.getenv("INFLUX_TIMEOUT_SECONDS", "8"))),
        )


class InfluxService:
    def __init__(self, config: InfluxConfig):
        self.config = config
        self.session = requests.Session()

    def is_available(self) -> Tuple[bool, str]:
        ok, _, error = self._run_sql("SELECT 1 AS ok")
        if ok:
            return True, "ok"
        return False, error

    def write_telemetry(self, rows: List[Dict[str, Any]]) -> Tuple[bool, str]:
        if not rows:
            return True, "no_data"

        payload_lines = []
        for row in rows:
            payload_lines.append(self._row_to_line_protocol(row))

        line_payload = "\n".join(payload_lines)
        attempts = self._write_attempts(line_payload)

        errors: List[str] = []
        for method, url, kwargs in attempts:
            try:
                response = self.session.request(
                    method=method,
                    url=url,
                    timeout=self.config.timeout_seconds,
                    **kwargs,
                )
            except requests.RequestException as exc:
                errors.append(f"{url} -> {exc}")
                continue

            if response.status_code < 300:
                return True, "ok"

            body = response.text.strip().replace("\n", " ")
            errors.append(f"{url} -> HTTP {response.status_code}: {body[:220]}")

        return False, " | ".join(errors) if errors else "write_failed"

    def query_recent(
        self,
        limit: int = 50,
        produit_id: Optional[str] = None,
        device_id: Optional[str] = None,
    ) -> Tuple[bool, List[Dict[str, Any]], str]:
        limit = max(1, min(limit, 500))
        where_parts: List[str] = []

        if produit_id:
            where_parts.append(f"produit_id = {self._sql_literal(produit_id)}")
        if device_id:
            where_parts.append(f"device_id = {self._sql_literal(device_id)}")

        where_clause = f" WHERE {' AND '.join(where_parts)}" if where_parts else ""
        sql = (
            "SELECT time, device_id, produit_id, product, valeur, distance, "
            "temperature_c, humidite, etat_relais, angle_servo "
            f"FROM stock_data{where_clause} "
            "ORDER BY time DESC "
            f"LIMIT {limit}"
        )

        ok, raw_rows, error = self._run_sql(sql)
        if not ok:
            return False, [], error

        normalized_rows = [self._normalize_row(row) for row in raw_rows]
        normalized_rows = [row for row in normalized_rows if row is not None]
        normalized_rows.reverse()
        return True, normalized_rows, "ok"

    def query_products(self, limit: int = 300) -> Tuple[bool, List[Dict[str, str]], str]:
        limit = max(1, min(limit, 2000))
        sql = (
            "SELECT produit_id, product, time FROM stock_data "
            "ORDER BY time DESC "
            f"LIMIT {limit}"
        )
        ok, raw_rows, error = self._run_sql(sql)
        if not ok:
            return False, [], error

        products_by_id: Dict[str, str] = {}
        for row in raw_rows:
            produit_id = self._pick_ci(row, ["produit_id", "product_id", "tag_produit_id"])
            product = self._pick_ci(row, ["product", "produit", "product_name"])
            if not produit_id:
                continue
            product_label = str(product).strip() if product else str(produit_id)
            if produit_id not in products_by_id:
                products_by_id[str(produit_id)] = product_label

        records = [
            {"produit_id": produit_id, "product": product}
            for produit_id, product in sorted(products_by_id.items(), key=lambda item: item[0])
        ]
        return True, records, "ok"

    def _run_sql(self, sql: str) -> Tuple[bool, List[Dict[str, Any]], str]:
        attempts = self._query_attempts(sql)
        errors: List[str] = []

        for method, url, kwargs in attempts:
            try:
                response = self.session.request(
                    method=method,
                    url=url,
                    timeout=self.config.timeout_seconds,
                    **kwargs,
                )
            except requests.RequestException as exc:
                errors.append(f"{url} -> {exc}")
                continue

            if response.status_code >= 300:
                body = response.text.strip().replace("\n", " ")
                errors.append(f"{url} -> HTTP {response.status_code}: {body[:220]}")
                continue

            rows = self._extract_rows_from_response(response)
            if rows is None:
                content_preview = response.text[:220].replace("\n", " ")
                errors.append(f"{url} -> format inconnu: {content_preview}")
                continue

            return True, rows, "ok"

        return False, [], " | ".join(errors) if errors else "query_failed"

    def _write_attempts(self, payload: str) -> List[Tuple[str, str, Dict[str, Any]]]:
        headers = self._headers(content_type="text/plain")
        base = self.config.base_url.rstrip("/")

        attempts: List[Tuple[str, str, Dict[str, Any]]] = [
            (
                "POST",
                f"{base}/api/v3/write_lp?db={self.config.database}&precision=ms",
                {"data": payload, "headers": headers},
            ),
            (
                "POST",
                f"{base}/api/v3/write_lp?database={self.config.database}&precision=ms",
                {"data": payload, "headers": headers},
            ),
        ]

        v2_url = f"{base}/api/v2/write?bucket={self.config.bucket}&precision=ms"
        if self.config.org:
            v2_url = f"{v2_url}&org={self.config.org}"
        attempts.append(("POST", v2_url, {"data": payload, "headers": headers}))

        return attempts

    def _query_attempts(self, sql: str) -> List[Tuple[str, str, Dict[str, Any]]]:
        base = self.config.base_url.rstrip("/")

        text_headers = self._headers(content_type="text/plain")
        json_headers = self._headers(content_type="application/json")

        attempts: List[Tuple[str, str, Dict[str, Any]]] = [
            (
                "POST",
                f"{base}/api/v3/query_sql?db={self.config.database}",
                {"data": sql, "headers": text_headers},
            ),
            (
                "POST",
                f"{base}/api/v3/query_sql?database={self.config.database}",
                {"data": sql, "headers": text_headers},
            ),
            (
                "POST",
                f"{base}/api/v3/query_sql",
                {"json": {"db": self.config.database, "q": sql}, "headers": json_headers},
            ),
            (
                "GET",
                f"{base}/api/v3/query_sql?db={self.config.database}&q={requests.utils.quote(sql, safe='')}",
                {"headers": self._headers()},
            ),
        ]

        return attempts

    def _headers(self, content_type: Optional[str] = None) -> Dict[str, str]:
        headers = {
            "Accept": "application/json,text/csv;q=0.9,text/plain;q=0.8,*/*;q=0.1",
        }
        if content_type:
            headers["Content-Type"] = content_type
        if self.config.token:
            headers["Authorization"] = f"Bearer {self.config.token}"
        return headers

    def _extract_rows_from_response(self, response: requests.Response) -> Optional[List[Dict[str, Any]]]:
        content_type = response.headers.get("Content-Type", "").lower()

        if "application/json" in content_type or "+json" in content_type:
            try:
                payload = response.json()
            except ValueError:
                return None
            return self._extract_rows_from_payload(payload)

        text = response.text.strip()
        if not text:
            return []

        try:
            payload = json.loads(text)
            rows = self._extract_rows_from_payload(payload)
            if rows is not None:
                return rows
        except ValueError:
            pass

        if "," in text and "\n" in text:
            reader = csv.DictReader(StringIO(text))
            if reader.fieldnames:
                return [dict(row) for row in reader]

        return None

    def _extract_rows_from_payload(self, payload: Any) -> Optional[List[Dict[str, Any]]]:
        if isinstance(payload, list):
            if not payload:
                return []
            if isinstance(payload[0], dict):
                return payload
            return None

        if not isinstance(payload, dict):
            return None

        for key in ("rows", "data", "records", "results"):
            value = payload.get(key)
            if isinstance(value, list):
                if not value:
                    return []
                if isinstance(value[0], dict):
                    return value
                if isinstance(value[0], list):
                    columns = payload.get("columns")
                    if isinstance(columns, list) and all(isinstance(col, str) for col in columns):
                        return [dict(zip(columns, row)) for row in value]

        columns = payload.get("columns")
        values = payload.get("values")
        if isinstance(columns, list) and isinstance(values, list):
            try:
                return [dict(zip(columns, row)) for row in values]
            except Exception:
                return None

        if "series" in payload and isinstance(payload["series"], list):
            rows: List[Dict[str, Any]] = []
            for serie in payload["series"]:
                if not isinstance(serie, dict):
                    continue
                serie_columns = serie.get("columns")
                serie_values = serie.get("values")
                if isinstance(serie_columns, list) and isinstance(serie_values, list):
                    rows.extend(dict(zip(serie_columns, row)) for row in serie_values)
            return rows

        return None

    def _normalize_row(self, row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        try:
            time_value = self._pick_ci(row, ["time", "_time", "timestamp"])
            device_id = self._pick_ci(row, ["device_id"]) or "esp32-default"
            produit_id = self._pick_ci(row, ["produit_id", "product_id", "tag_produit_id"])
            product = self._pick_ci(row, ["product", "produit", "product_name"])
            valeur = self._to_int(self._pick_ci(row, ["valeur", "stock_percent", "value"]))
            distance_cm = self._to_float(self._pick_ci(row, ["distance_cm", "distance"]))
            temperature_c = self._to_float(self._pick_ci(row, ["temperature_c", "temperature"]))
            humidity_pct = self._to_float(self._pick_ci(row, ["humidity_pct", "humidity", "humidite"]))
            actuator_on = self._to_bool(
                self._pick_ci(row, ["actuator_on", "actuator", "actuator_state", "etat_relais"])
            )
            angle_servo = self._to_int(self._pick_ci(row, ["angle_servo", "servo_angle"]))

            if produit_id is None:
                produit_id = "produit-1"
            if product is None:
                product = str(produit_id)
            if valeur is None:
                valeur = 0
            if angle_servo is None:
                angle_servo = 90 if actuator_on else 0

            timestamp = self._to_datetime(time_value)
            date_text = timestamp.strftime("%H:%M:%S")

            return {
                "id": None,
                "date": date_text,
                "timestamp": timestamp.isoformat(),
                "timestamp_ms": int(timestamp.timestamp() * 1000),
                "device_id": str(device_id),
                "produit_id": str(produit_id),
                "product": str(product),
                "valeur": max(0, min(100, int(valeur))),
                "distance_cm": distance_cm,
                "temperature_c": temperature_c,
                "humidity_pct": humidity_pct,
                "etat_relais": 1 if actuator_on else 0,
                "angle_servo": max(0, min(180, int(angle_servo))),
                "actuator_state": "on" if actuator_on else "off",
            }
        except Exception as exc:
            logger.debug("skip malformed Influx row: %s", exc)
            return None

    def _row_to_line_protocol(self, row: Dict[str, Any]) -> str:
        measurement = "stock_data"
        timestamp_ms = int(row.get("timestamp_ms") or int(datetime.now(tz=timezone.utc).timestamp() * 1000))

        tags = {
            "produit_id": row.get("produit_id", "produit-1"),
            "device_id": row.get("device_id", "esp32-default"),
            "product": row.get("product", "Produit"),
        }

        fields: List[str] = []

        distance_value = self._to_float(row.get("distance"))
        if distance_value is None:
            distance_value = self._to_float(row.get("distance_cm"))
        if distance_value is None:
            distance_value = 0.0
        fields.append(f"distance={distance_value}")

        humidity_value = self._to_float(row.get("humidite"))
        if humidity_value is None:
            humidity_value = self._to_float(row.get("humidity_pct"))
        if humidity_value is not None:
            fields.append(f"humidite={humidity_value}")

        relay_state = self._to_int(row.get("etat_relais"))
        if relay_state is None:
            actuator_state = str(row.get("actuator_state", "off")).lower()
            relay_state = 1 if actuator_state == "on" else 0
        relay_state = 1 if relay_state and relay_state > 0 else 0
        fields.append(f"etat_relais={relay_state}i")

        servo_angle = self._to_int(row.get("angle_servo"))
        if servo_angle is None:
            servo_angle = 90 if relay_state == 1 else 0
        servo_angle = max(0, min(180, int(servo_angle)))
        fields.append(f"angle_servo={servo_angle}i")

        valeur = self._to_int(row.get("valeur"))
        if valeur is None:
            valeur = 0
        fields.append(f"valeur={max(0, min(100, valeur))}i")

        temperature_c = self._to_float(row.get("temperature_c"))
        if temperature_c is not None:
            fields.append(f"temperature_c={temperature_c}")

        tag_text = ",".join(
            f"{self._escape_tag_key(key)}={self._escape_tag_value(str(value))}"
            for key, value in tags.items()
            if value is not None
        )
        field_text = ",".join(fields)

        return f"{measurement},{tag_text} {field_text} {timestamp_ms}"

    def _pick_ci(self, row: Dict[str, Any], keys: List[str]) -> Any:
        lowered = {str(key).lower(): value for key, value in row.items()}
        for key in keys:
            if key.lower() in lowered:
                return lowered[key.lower()]
        return None

    def _to_int(self, value: Any) -> Optional[int]:
        if value is None or value == "":
            return None
        if isinstance(value, bool):
            return int(value)
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return None

    def _to_float(self, value: Any) -> Optional[float]:
        if value is None or value == "":
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def _to_bool(self, value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if value is None:
            return False
        text = str(value).strip().lower()
        return text in {"1", "true", "on", "yes"}

    def _to_datetime(self, value: Any) -> datetime:
        if value is None or value == "":
            return datetime.now(tz=timezone.utc)

        if isinstance(value, datetime):
            if value.tzinfo is None:
                return value.replace(tzinfo=timezone.utc)
            return value.astimezone(timezone.utc)

        if isinstance(value, (int, float)):
            number = float(value)
            if number > 10_000_000_000:
                number /= 1000.0
            return datetime.fromtimestamp(number, tz=timezone.utc)

        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
            if parsed.tzinfo is None:
                return parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except ValueError:
            return datetime.now(tz=timezone.utc)

    def _sql_literal(self, value: str) -> str:
        escaped = str(value).replace("'", "''")
        return f"'{escaped}'"

    def _escape_tag_key(self, value: str) -> str:
        return value.replace("\\", "\\\\").replace(",", "\\,").replace(" ", "\\ ").replace("=", "\\=")

    def _escape_tag_value(self, value: str) -> str:
        return value.replace("\\", "\\\\").replace(",", "\\,").replace(" ", "\\ ").replace("=", "\\=")
