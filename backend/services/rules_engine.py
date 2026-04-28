from dataclasses import dataclass, asdict
from typing import List, Optional, Dict
import os


LEVEL_NORMAL = "Normal"
LEVEL_ALERT = "Alerte"
LEVEL_CRITICAL = "Critique"


@dataclass
class RuleThresholds:
    stock_warning: int = 35
    stock_critical: int = 20
    temp_warning_c: float = 30.0
    temp_critical_c: float = 36.0
    humidity_low_warning: float = 25.0
    humidity_high_warning: float = 75.0
    humidity_low_critical: float = 15.0
    humidity_high_critical: float = 85.0
    combination_temp_boost_c: float = 32.0

    @classmethod
    def from_env(cls) -> "RuleThresholds":
        def _get(name: str, default, caster):
            raw = os.getenv(name)
            if raw is None:
                return default
            try:
                return caster(raw)
            except (TypeError, ValueError):
                return default

        return cls(
            stock_warning=_get("ALERT_STOCK_WARNING", 35, int),
            stock_critical=_get("ALERT_STOCK_CRITICAL", 20, int),
            temp_warning_c=_get("ALERT_TEMP_WARNING_C", 30.0, float),
            temp_critical_c=_get("ALERT_TEMP_CRITICAL_C", 36.0, float),
            humidity_low_warning=_get("ALERT_HUMIDITY_LOW_WARNING", 25.0, float),
            humidity_high_warning=_get("ALERT_HUMIDITY_HIGH_WARNING", 75.0, float),
            humidity_low_critical=_get("ALERT_HUMIDITY_LOW_CRITICAL", 15.0, float),
            humidity_high_critical=_get("ALERT_HUMIDITY_HIGH_CRITICAL", 85.0, float),
            combination_temp_boost_c=_get("ALERT_COMBINATION_TEMP_BOOST_C", 32.0, float),
        )

    def to_dict(self) -> Dict[str, float]:
        return asdict(self)


@dataclass
class CrisisInput:
    stock_percent: int
    temperature_c: Optional[float] = None
    humidity_pct: Optional[float] = None


@dataclass
class CrisisEvaluation:
    level: str
    alert_type: str
    reasons: List[str]
    recommendations: List[str]
    risk_score: int


class RulesEngine:
    _severity_rank = {
        LEVEL_NORMAL: 0,
        LEVEL_ALERT: 1,
        LEVEL_CRITICAL: 2,
    }

    def evaluate(self, snapshot: CrisisInput, thresholds: RuleThresholds) -> CrisisEvaluation:
        level = LEVEL_NORMAL
        reasons: List[str] = []
        recommendations: List[str] = []
        alert_type = "normal"
        risk_score = 0

        stock_level = LEVEL_NORMAL
        if snapshot.stock_percent <= thresholds.stock_critical:
            stock_level = LEVEL_CRITICAL
            reasons.append(
                f"Stock critique ({snapshot.stock_percent}% <= {thresholds.stock_critical}%)"
            )
            recommendations.append("Reapprovisionner immediatement ce produit")
            alert_type = "stock_faible"
            risk_score += 55
        elif snapshot.stock_percent <= thresholds.stock_warning:
            stock_level = LEVEL_ALERT
            reasons.append(
                f"Stock faible ({snapshot.stock_percent}% <= {thresholds.stock_warning}%)"
            )
            recommendations.append("Planifier un reapprovisionnement dans les prochaines heures")
            alert_type = "stock_faible"
            risk_score += 30

        temp_level = LEVEL_NORMAL
        if snapshot.temperature_c is not None:
            if snapshot.temperature_c >= thresholds.temp_critical_c:
                temp_level = LEVEL_CRITICAL
                reasons.append(
                    f"Temperature critique ({snapshot.temperature_c:.1f}C >= {thresholds.temp_critical_c:.1f}C)"
                )
                recommendations.append("Verifier ventilation et capteur temperature")
                alert_type = "temperature_elevee"
                risk_score += 45
            elif snapshot.temperature_c >= thresholds.temp_warning_c:
                temp_level = LEVEL_ALERT
                reasons.append(
                    f"Temperature elevee ({snapshot.temperature_c:.1f}C >= {thresholds.temp_warning_c:.1f}C)"
                )
                recommendations.append("Activer refroidissement preventif")
                alert_type = "temperature_elevee"
                risk_score += 25

        humidity_level = LEVEL_NORMAL
        if snapshot.humidity_pct is not None:
            if (
                snapshot.humidity_pct <= thresholds.humidity_low_critical
                or snapshot.humidity_pct >= thresholds.humidity_high_critical
            ):
                humidity_level = LEVEL_CRITICAL
                reasons.append(
                    "Humidite critique "
                    f"({snapshot.humidity_pct:.1f}% hors [{thresholds.humidity_low_critical:.1f}, {thresholds.humidity_high_critical:.1f}])"
                )
                recommendations.append("Inspecter stockage et calibration humidite")
                alert_type = "humidite_anormale"
                risk_score += 30
            elif (
                snapshot.humidity_pct <= thresholds.humidity_low_warning
                or snapshot.humidity_pct >= thresholds.humidity_high_warning
            ):
                humidity_level = LEVEL_ALERT
                reasons.append(
                    "Humidite anormale "
                    f"({snapshot.humidity_pct:.1f}% hors [{thresholds.humidity_low_warning:.1f}, {thresholds.humidity_high_warning:.1f}])"
                )
                recommendations.append("Surveiller humidite et verifier capteur")
                alert_type = "humidite_anormale"
                risk_score += 15

        level = self._max_level(stock_level, temp_level, humidity_level)

        # Combination rule: low stock + high temperature escalates quickly.
        if (
            snapshot.stock_percent <= thresholds.stock_warning
            and snapshot.temperature_c is not None
            and snapshot.temperature_c >= thresholds.combination_temp_boost_c
        ):
            level = LEVEL_CRITICAL
            alert_type = "combinaison_critique"
            reasons.append(
                "Combinaison critique: stock bas et temperature haute"
            )
            recommendations.append("Prioriser reapprovisionnement et refroidissement du stock")
            risk_score = max(risk_score, 90)

        if level == LEVEL_NORMAL:
            reasons.append("Aucune anomalie detectee")
            recommendations.append("Aucune action requise")

        # Keep only unique recommendations while preserving order.
        unique_recommendations = list(dict.fromkeys(recommendations))

        risk_score = max(0, min(100, risk_score))

        return CrisisEvaluation(
            level=level,
            alert_type=alert_type,
            reasons=reasons,
            recommendations=unique_recommendations,
            risk_score=risk_score,
        )

    def _max_level(self, *levels: str) -> str:
        return max(levels, key=lambda level: self._severity_rank.get(level, 0))
