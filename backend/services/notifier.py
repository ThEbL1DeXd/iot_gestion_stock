from dataclasses import dataclass
from datetime import datetime
from email.message import EmailMessage
from typing import List, Optional
import json
import logging
import os
import ssl
import smtplib
import urllib.error
import urllib.request


logger = logging.getLogger("smart_stock.notifier")


@dataclass
class NotificationContext:
    alert_id: int
    level: str
    alert_type: str
    product: str
    valeur: int
    created_at: datetime
    reasons: str
    recommendation: str
    risk_score: int
    temperature_c: Optional[float] = None
    humidity_pct: Optional[float] = None


class NotificationService:
    def __init__(self, default_cooldown_seconds: int = 300):
        self.default_cooldown_seconds = default_cooldown_seconds

    @staticmethod
    def _is_truthy_env(name: str, default: bool = False) -> bool:
        raw = os.getenv(name)
        if raw is None:
            return default
        return raw.strip().lower() in {"1", "true", "yes", "on"}

    @staticmethod
    def _is_tls_verification_error(exc: Exception) -> bool:
        if isinstance(exc, ssl.SSLCertVerificationError):
            return True
        message = str(exc)
        return (
            "CERTIFICATE_VERIFY_FAILED" in message
            or "self-signed certificate in certificate chain" in message
        )

    def _telegram_ssl_context(self) -> ssl.SSLContext:
        if self._is_truthy_env("TELEGRAM_SSL_NO_VERIFY", default=False):
            logger.warning(
                "Telegram TLS verification disabled by TELEGRAM_SSL_NO_VERIFY=true. "
                "Use only for local/debug environments."
            )
            return ssl._create_unverified_context()

        ca_bundle = os.getenv("TELEGRAM_CA_BUNDLE")
        if ca_bundle:
            try:
                return ssl.create_default_context(cafile=ca_bundle)
            except Exception as exc:
                logger.warning(
                    "Telegram CA bundle invalid (%s): %s. Falling back to default trust store.",
                    ca_bundle,
                    exc,
                )

        return ssl.create_default_context()

    def build_fingerprint(self, level: str, alert_type: str, product: str) -> str:
        return f"{level}|{alert_type}|{product.strip().lower()}"

    def notify(self, context: NotificationContext) -> List[str]:
        sent_channels: List[str] = []

        if self._send_email(context):
            sent_channels.append("email")

        if self._send_webhook(context):
            sent_channels.append("webhook")

        if self._send_telegram_alert(context):
            sent_channels.append("telegram")

        return sent_channels

    def send_telegram_message(self, message: str) -> bool:
        token = os.getenv("TELEGRAM_TOKEN")
        chat_id = os.getenv("TELEGRAM_CHAT_ID")

        if not token or not chat_id:
            missing = []
            if not token:
                missing.append("TELEGRAM_TOKEN")
            if not chat_id:
                missing.append("TELEGRAM_CHAT_ID")
            logger.warning("Telegram skipped: missing env vars: %s", ", ".join(missing))
            return False

        payload = {
            "chat_id": chat_id,
            "text": message,
        }
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        ssl_context = self._telegram_ssl_context()

        try:
            request = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=8, context=ssl_context) as response:
                return int(getattr(response, "status", 0)) < 300
        except Exception as exc:
            reason = getattr(exc, "reason", exc)
            if self._is_tls_verification_error(reason if isinstance(reason, Exception) else exc):
                logger.warning(
                    "Telegram send failed: %s. "
                    "If your network uses SSL inspection, set TELEGRAM_CA_BUNDLE to a PEM root CA "
                    "or TELEGRAM_SSL_NO_VERIFY=true for local debug only.",
                    exc,
                )
                return False
            logger.warning("Telegram send failed: %s", exc)
            return False

    def _send_email(self, context: NotificationContext) -> bool:
        smtp_host = os.getenv("ALERT_SMTP_HOST")
        smtp_to = os.getenv("ALERT_EMAIL_TO")

        if not smtp_host or not smtp_to:
            return False

        smtp_port = int(os.getenv("ALERT_SMTP_PORT", "587"))
        smtp_user = os.getenv("ALERT_SMTP_USER")
        smtp_password = os.getenv("ALERT_SMTP_PASSWORD")
        smtp_from = os.getenv("ALERT_EMAIL_FROM", smtp_user or "iot-alert@localhost")

        message = EmailMessage()
        message["Subject"] = f"[{context.level}] Alerte Smart Stock - {context.product}"
        message["From"] = smtp_from
        message["To"] = smtp_to

        body = [
            "Alerte Smart Stock",
            "",
            f"Produit: {context.product}",
            f"Type: {context.alert_type}",
            f"Niveau: {context.level}",
            f"Score de risque: {context.risk_score}/100",
            f"Valeur stock: {context.valeur}%",
            f"Date: {context.created_at.strftime('%Y-%m-%d %H:%M:%S')}",
            f"Raison: {context.reasons}",
            f"Recommandation: {context.recommendation}",
        ]

        if context.temperature_c is not None:
            body.append(f"Temperature: {context.temperature_c:.1f} C")
        if context.humidity_pct is not None:
            body.append(f"Humidite: {context.humidity_pct:.1f}%")

        message.set_content("\n".join(body))

        try:
            with smtplib.SMTP(smtp_host, smtp_port, timeout=12) as server:
                server.starttls()
                if smtp_user and smtp_password:
                    server.login(smtp_user, smtp_password)
                server.send_message(message)
            return True
        except Exception:
            return False

    def _send_webhook(self, context: NotificationContext) -> bool:
        webhook_url = os.getenv("ALERT_WEBHOOK_URL")
        if not webhook_url:
            return False

        payload = {
            "alert_id": context.alert_id,
            "level": context.level,
            "type": context.alert_type,
            "product": context.product,
            "valeur": context.valeur,
            "risk_score": context.risk_score,
            "temperature_c": context.temperature_c,
            "humidity_pct": context.humidity_pct,
            "date": context.created_at.strftime("%Y-%m-%d %H:%M:%S"),
            "reasons": context.reasons,
            "recommendation": context.recommendation,
        }

        try:
            request = urllib.request.Request(
                webhook_url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=8):
                return True
        except Exception:
            return False

    def _send_telegram_alert(self, context: NotificationContext) -> bool:
        lines = [
            f"Smart Stock [{context.level}]",
            f"Produit: {context.product}",
            f"Type: {context.alert_type}",
            f"Stock: {context.valeur}%",
            f"Risque: {context.risk_score}/100",
        ]

        if context.humidity_pct is not None:
            lines.append(f"Humidite: {context.humidity_pct:.1f}%")
        if context.temperature_c is not None:
            lines.append(f"Temperature: {context.temperature_c:.1f}C")

        lines.append(f"Raison: {context.reasons}")
        lines.append(f"Action: {context.recommendation}")
        return self.send_telegram_message("\n".join(lines))
