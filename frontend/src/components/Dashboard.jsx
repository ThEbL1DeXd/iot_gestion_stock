import { useEffect, useMemo, useState } from 'react';
import axios from 'axios';
import { Line } from 'react-chartjs-2';
import 'chart.js/auto';
import { AnimatePresence, motion } from 'framer-motion';
import {
  BellRing,
  Boxes,
  Cpu,
  Droplets,
  Gauge,
  MoonStar,
  Power,
  RefreshCw,
  Ruler,
  ShieldAlert,
  SlidersHorizontal,
  Sun,
  Thermometer,
  TriangleAlert,
  Wifi,
  WifiOff
} from 'lucide-react';

const API_BASE = (import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000').replace(/\/+$/, '');
const WS_URL = `${API_BASE.replace(/^http/, 'ws')}/ws`;
const MAX_POINTS = 220;
const MAX_ALERTS = 100;

const LEVEL_META = {
  Normal: {
    label: 'Normal',
    badge: 'border border-emerald-400/40 bg-emerald-500/20 text-emerald-200',
    panel: 'border border-emerald-400/30 bg-emerald-500/15 text-emerald-100',
    chartBorder: '#34d399',
    chartFill: 'rgba(16, 185, 129, 0.2)'
  },
  Alerte: {
    label: 'Alerte',
    badge: 'border border-amber-300/50 bg-amber-500/20 text-amber-100',
    panel: 'border border-amber-300/50 bg-amber-500/20 text-amber-100',
    chartBorder: '#f59e0b',
    chartFill: 'rgba(245, 158, 11, 0.2)'
  },
  Critique: {
    label: 'Critique',
    badge: 'border border-rose-400/40 bg-rose-500/20 text-rose-200',
    panel: 'border border-rose-400/40 bg-rose-500/25 text-rose-100',
    chartBorder: '#f43f5e',
    chartFill: 'rgba(244, 63, 94, 0.22)'
  }
};

const defaultActuator = {
  mode: 'auto',
  state: 'off',
  humidity_threshold_pct: 75,
  updated_at: '--',
  reason: 'startup'
};

const parseNumber = (value) => {
  if (value === undefined || value === null || value === '') {
    return null;
  }

  const num = Number(value);
  if (Number.isNaN(num)) {
    return null;
  }

  return num;
};

const estimateDistance = (stockPercent) => Number((2 + ((100 - stockPercent) / 100) * 398).toFixed(1));

const getStatus = (stockPercent) => {
  if (stockPercent < 20) {
    return {
      label: 'Vide',
      badge: 'border border-rose-400/40 bg-rose-500/20 text-rose-200'
    };
  }

  if (stockPercent < 60) {
    return {
      label: 'Moyen',
      badge: 'border border-amber-300/50 bg-amber-500/20 text-amber-100'
    };
  }

  return {
    label: 'Plein',
    badge: 'border border-emerald-400/40 bg-emerald-500/20 text-emerald-200'
  };
};

const formatDate = (value) => {
  if (!value) {
    return new Date().toLocaleTimeString('fr-FR', { hour12: false });
  }

  const text = String(value);
  if (/^\d{2}:\d{2}:\d{2}$/.test(text)) {
    return text;
  }

  const asDate = new Date(text);
  if (Number.isNaN(asDate.getTime())) {
    return text;
  }

  return asDate.toLocaleTimeString('fr-FR', { hour12: false });
};

const formatDateTime = (value) => {
  if (!value) {
    return '--';
  }

  const asDate = new Date(String(value));
  if (Number.isNaN(asDate.getTime())) {
    return String(value);
  }

  return asDate.toLocaleString('fr-FR', { hour12: false });
};

const inferLevel = (value) => {
  if (value <= 20) {
    return 'Critique';
  }
  if (value <= 35) {
    return 'Alerte';
  }
  return 'Normal';
};

const normalizeRow = (row) => {
  const valeur = Number(row?.valeur ?? 0);
  const distanceRaw = parseNumber(row?.distance_cm);
  const distance = distanceRaw === null ? estimateDistance(valeur) : Number(distanceRaw.toFixed(1));

  const produitId = String(row?.produit_id ?? row?.product ?? 'produit-1');
  const product = String(row?.product ?? produitId);

  return {
    id: row?.id ?? `${produitId}-${row?.timestamp_ms ?? Date.now()}`,
    timestamp: row?.timestamp ?? null,
    timestamp_ms: row?.timestamp_ms ?? null,
    date: formatDate(row?.date ?? row?.timestamp),
    valeur,
    distance,
    status: getStatus(valeur).label,
    level: row?.level ?? inferLevel(valeur),
    produit_id: produitId,
    product,
    device_id: String(row?.device_id ?? 'esp32-default'),
    temperature_c: parseNumber(row?.temperature_c),
    humidity_pct: parseNumber(row?.humidity_pct),
    actuator_state: String(row?.actuator_state ?? row?.actuator?.state ?? 'off').toLowerCase()
  };
};

const normalizeAlert = (alert) => ({
  id: alert?.id ?? Date.now(),
  level: alert?.level ?? 'Alerte',
  alert_type: alert?.alert_type ?? 'inconnu',
  product: alert?.product ?? 'Produit principal',
  produit_id: alert?.produit_id ?? 'produit-1',
  valeur: Number(alert?.valeur ?? 0),
  temperature_c: parseNumber(alert?.temperature_c),
  humidity_pct: parseNumber(alert?.humidity_pct),
  reasons: Array.isArray(alert?.reasons) ? alert.reasons.join('; ') : String(alert?.reasons ?? ''),
  recommendation: String(alert?.recommendation ?? ''),
  risk_score: Number(alert?.risk_score ?? 0),
  cooldown_until: alert?.cooldown_until ?? null,
  sent_channels: Array.isArray(alert?.sent_channels) ? alert.sent_channels : [],
  notification_suppressed: Boolean(alert?.notification_suppressed),
  acknowledged: Boolean(alert?.acknowledged),
  created_at: alert?.created_at ?? new Date().toLocaleString('fr-FR')
});

const normalizeProduct = (entry) => {
  const produitId = String(entry?.produit_id ?? entry?.product ?? 'produit-1');
  const product = String(entry?.product ?? produitId);
  return { produit_id: produitId, product };
};

const normalizeActuator = (payload) => ({
  mode: payload?.mode === 'manual' ? 'manual' : 'auto',
  state: payload?.state === 'on' ? 'on' : 'off',
  humidity_threshold_pct: parseNumber(payload?.humidity_threshold_pct) ?? 75,
  updated_at: payload?.updated_at ?? '--',
  reason: payload?.reason ?? ''
});

const normalizeDepletionForecast = (payload, fallbackProduitId = 'produit-1', fallbackProduct = 'Produit') => ({
  status: String(payload?.status ?? 'insufficient_data'),
  produit_id: String(payload?.produit_id ?? fallbackProduitId),
  product: String(payload?.product ?? fallbackProduct),
  days_to_empty: parseNumber(payload?.days_to_empty),
  estimated_empty_at: payload?.estimated_empty_at ?? null,
  consumption_rate_pct_per_day: parseNumber(payload?.consumption_rate_pct_per_day),
  model_r2: parseNumber(payload?.model_r2),
  risk_level: String(payload?.risk_level ?? 'Normal'),
  risk_window_days: parseNumber(payload?.risk_window_days) ?? 3,
  message: String(payload?.message ?? ''),
  source: String(payload?.source ?? '')
});

const getLevelMeta = (level) => LEVEL_META[level] ?? LEVEL_META.Normal;

export default function Dashboard() {
  const [history, setHistory] = useState([]);
  const [alertHistory, setAlertHistory] = useState([]);
  const [thresholds, setThresholds] = useState(null);
  const [products, setProducts] = useState([]);
  const [selectedProduct, setSelectedProduct] = useState('');
  const [prediction, setPrediction] = useState('N/A');
  const [forecastLevel, setForecastLevel] = useState('N/A');
  const [currentSeverity, setCurrentSeverity] = useState('Normal');
  const [currentRiskScore, setCurrentRiskScore] = useState(0);
  const [currentRecommendation, setCurrentRecommendation] = useState('');
  const [forecastByProduct, setForecastByProduct] = useState({});
  const [actuator, setActuator] = useState(defaultActuator);
  const [thresholdInput, setThresholdInput] = useState('75');
  const [isSavingActuator, setIsSavingActuator] = useState(false);
  const [isConnected, setIsConnected] = useState(false);
  const [reconnectDelayMs, setReconnectDelayMs] = useState(0);
  const [isDark, setIsDark] = useState(true);
  const [notice, setNotice] = useState('');

  useEffect(() => {
    const savedTheme = localStorage.getItem('dashboard-theme');
    if (savedTheme === 'light') {
      setIsDark(false);
    }
  }, []);

  useEffect(() => {
    document.documentElement.classList.toggle('dark', isDark);
    localStorage.setItem('dashboard-theme', isDark ? 'dark' : 'light');
  }, [isDark]);

  const mergeProductsFromRows = (rows) => {
    if (!Array.isArray(rows) || rows.length === 0) {
      return;
    }

    setProducts((prev) => {
      const byId = new Map(prev.map((item) => [item.produit_id, item]));
      rows.forEach((row) => {
        byId.set(row.produit_id, { produit_id: row.produit_id, product: row.product });
      });
      return Array.from(byId.values()).sort((a, b) => a.produit_id.localeCompare(b.produit_id));
    });
  };

  const pushRows = (incomingRows) => {
    if (!Array.isArray(incomingRows) || incomingRows.length === 0) {
      return;
    }

    const normalizedRows = incomingRows.map(normalizeRow);
    mergeProductsFromRows(normalizedRows);

    setHistory((prev) => {
      const merged = [...prev, ...normalizedRows];
      if (merged.length <= MAX_POINTS) {
        return merged;
      }
      return merged.slice(-MAX_POINTS);
    });

    if (!selectedProduct && normalizedRows.length > 0) {
      setSelectedProduct(normalizedRows[0].produit_id);
    }
  };

  useEffect(() => {
    let mounted = true;

    const loadInitialData = async () => {
      try {
        const [historyResponse, alertsResponse, configResponse, productsResponse, actuatorResponse] =
          await Promise.all([
            axios.get(`${API_BASE}/data?limit=220`),
            axios.get(`${API_BASE}/alerts?limit=100`),
            axios.get(`${API_BASE}/alerts/config`),
            axios.get(`${API_BASE}/products`),
            axios.get(`${API_BASE}/actuator/state`)
          ]);

        if (!mounted) {
          return;
        }

        const rows = (Array.isArray(historyResponse.data) ? historyResponse.data : [])
          .map(normalizeRow)
          .slice(-MAX_POINTS);
        setHistory(rows);

        const normalizedProducts = (Array.isArray(productsResponse.data) ? productsResponse.data : [])
          .map(normalizeProduct)
          .filter((item, index, arr) => arr.findIndex((x) => x.produit_id === item.produit_id) === index);

        if (rows.length > 0) {
          const rowProducts = rows.map((row) => ({ produit_id: row.produit_id, product: row.product }));
          const allProducts = [...normalizedProducts, ...rowProducts];
          const byId = new Map(allProducts.map((item) => [item.produit_id, item]));
          const mergedProducts = Array.from(byId.values()).sort((a, b) =>
            a.produit_id.localeCompare(b.produit_id)
          );
          setProducts(mergedProducts);
          setSelectedProduct((current) => current || mergedProducts[0]?.produit_id || '');
        } else {
          setProducts(normalizedProducts);
          setSelectedProduct((current) => current || normalizedProducts[0]?.produit_id || '');
        }

        const alerts = (Array.isArray(alertsResponse.data) ? alertsResponse.data : [])
          .map(normalizeAlert)
          .slice(0, MAX_ALERTS);
        setAlertHistory(alerts);

        if (configResponse?.data && typeof configResponse.data === 'object') {
          setThresholds(configResponse.data);
        }

        const actuatorState = normalizeActuator(actuatorResponse?.data ?? defaultActuator);
        setActuator(actuatorState);
        setThresholdInput(String(actuatorState.humidity_threshold_pct));
      } catch (error) {
        console.error('Erreur chargement initial:', error);
        setNotice('Impossible de charger les donnees initiales.');
      }
    };

    loadInitialData();

    return () => {
      mounted = false;
    };
  }, []);

  useEffect(() => {
    let ws;
    let reconnectTimer;
    let pingInterval;
    let reconnectAttempts = 0;
    let isDisposed = false;

    const connect = () => {
      ws = new WebSocket(WS_URL);

      ws.onopen = () => {
        setIsConnected(true);
        setReconnectDelayMs(0);
        reconnectAttempts = 0;

        pingInterval = setInterval(() => {
          if (ws && ws.readyState === WebSocket.OPEN) {
            ws.send('ping');
          }
        }, 15000);
      };

      ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          if (!data) {
            return;
          }

          if (data.event === 'heartbeat') {
            return;
          }

          if (data.event === 'actuator' && data.actuator) {
            const state = normalizeActuator(data.actuator);
            setActuator(state);
            setThresholdInput(String(state.humidity_threshold_pct));
            return;
          }

          if (data.event === 'alert' && data.alert) {
            const incomingAlert = normalizeAlert(data.alert);
            setAlertHistory((prev) =>
              [incomingAlert, ...prev.filter((item) => item.id !== incomingAlert.id)].slice(0, MAX_ALERTS)
            );
            return;
          }

          if (data.event === 'telemetry_batch' && Array.isArray(data.sensors)) {
            const rows = data.sensors.map(normalizeRow);
            pushRows(rows);

            const forecastEntries = data.sensors
              .filter((item) => item?.depletion_forecast)
              .map((item) => {
                const normalized = normalizeRow(item);
                return normalizeDepletionForecast(
                  item.depletion_forecast,
                  normalized.produit_id,
                  normalized.product
                );
              });

            if (forecastEntries.length > 0) {
              setForecastByProduct((prev) => {
                const next = { ...prev };
                forecastEntries.forEach((entry) => {
                  next[entry.produit_id] = entry;
                });
                return next;
              });
            }

            if (data.actuator) {
              const state = normalizeActuator(data.actuator);
              setActuator(state);
              setThresholdInput(String(state.humidity_threshold_pct));
            }

            const latest = rows.at(-1);
            if (latest) {
              setCurrentSeverity(latest.level ?? inferLevel(latest.valeur));
            }
            return;
          }

          if (data.event === 'telemetry' || data.valeur !== undefined) {
            const row = normalizeRow(data);
            pushRows([row]);

            if (data.depletion_forecast) {
              const forecast = normalizeDepletionForecast(data.depletion_forecast, row.produit_id, row.product);
              setForecastByProduct((prev) => ({
                ...prev,
                [forecast.produit_id]: forecast
              }));
            }

            setCurrentSeverity(data.level ?? inferLevel(row.valeur));
            setCurrentRiskScore(Number(data.risk_score ?? 0));
            setCurrentRecommendation(String(data.recommendation ?? ''));
            setForecastLevel(data.forecast_level ?? 'N/A');

            if (data.prediction !== undefined && data.prediction !== null) {
              setPrediction(data.prediction);
            }

            if (data.actuator) {
              const state = normalizeActuator(data.actuator);
              setActuator(state);
              setThresholdInput(String(state.humidity_threshold_pct));
            }

            if (data.level && data.level !== 'Normal') {
              const reasonText = Array.isArray(data.reasons)
                ? data.reasons.join('; ')
                : String(data.reasons ?? 'Alerte detectee');
              const recommendationText = data.recommendation ? ` | Action: ${data.recommendation}` : '';
              setNotice(`${data.level}: ${reasonText}${recommendationText}`);
              setTimeout(() => setNotice(''), 3500);
            }
          }
        } catch (error) {
          console.error('Erreur WebSocket:', error);
        }
      };

      ws.onerror = () => {
        setIsConnected(false);
      };

      ws.onclose = () => {
        setIsConnected(false);

        if (pingInterval) {
          clearInterval(pingInterval);
        }

        if (!isDisposed) {
          reconnectAttempts += 1;
          const delay = Math.min(1000 * 2 ** reconnectAttempts, 30000);
          setReconnectDelayMs(delay);
          reconnectTimer = setTimeout(connect, delay);
        }
      };
    };

    connect();

    return () => {
      isDisposed = true;

      if (reconnectTimer) {
        clearTimeout(reconnectTimer);
      }

      if (pingInterval) {
        clearInterval(pingInterval);
      }

      if (ws) {
        ws.close();
      }
    };
  }, [selectedProduct]);

  const filteredHistory = useMemo(() => {
    if (!selectedProduct) {
      return history;
    }
    return history.filter((item) => item.produit_id === selectedProduct);
  }, [history, selectedProduct]);

  const latestRow = filteredHistory.length > 0 ? filteredHistory.at(-1) : history.at(-1);

  const currentLevel = latestRow?.valeur ?? 0;
  const distanceCm = latestRow?.distance ?? estimateDistance(currentLevel);
  const temperatureC = latestRow?.temperature_c ?? null;
  const humidityPct = latestRow?.humidity_pct ?? null;
  const activeProductLabel =
    products.find((item) => item.produit_id === selectedProduct)?.product ?? latestRow?.product ?? '--';
  const activeForecast = selectedProduct ? forecastByProduct[selectedProduct] ?? null : null;

  const statusMeta = useMemo(() => getStatus(currentLevel), [currentLevel]);
  const levelMeta = useMemo(() => getLevelMeta(currentSeverity), [currentSeverity]);
  const isCritical = currentSeverity === 'Critique';

  const chartData = useMemo(
    () => ({
      labels: filteredHistory.map((item) => item.date),
      datasets: [
        {
          label: `Stock (%) - ${activeProductLabel}`,
          data: filteredHistory.map((item) => item.valeur),
          tension: 0.35,
          fill: true,
          borderWidth: 3,
          borderColor: levelMeta.chartBorder,
          backgroundColor: levelMeta.chartFill,
          pointRadius: 2,
          pointHoverRadius: 5,
          pointBackgroundColor: levelMeta.chartBorder
        }
      ]
    }),
    [filteredHistory, activeProductLabel, levelMeta]
  );

  const chartOptions = useMemo(
    () => ({
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: {
          labels: {
            color: '#cbd5e1'
          }
        }
      },
      scales: {
        x: {
          ticks: {
            color: '#94a3b8'
          },
          grid: {
            color: 'rgba(148, 163, 184, 0.15)'
          }
        },
        y: {
          min: 0,
          max: 100,
          ticks: {
            color: '#94a3b8'
          },
          grid: {
            color: 'rgba(148, 163, 184, 0.15)'
          }
        }
      }
    }),
    []
  );

  const tableRows = filteredHistory.slice(-14).reverse();
  const alertRows = alertHistory.slice(0, 10);

  const tempLabel =
    temperatureC === null || Number.isNaN(temperatureC) ? '--' : `${temperatureC.toFixed(1)} C`;
  const humidityLabel = humidityPct === null || Number.isNaN(humidityPct) ? '--' : `${humidityPct.toFixed(1)}%`;

  const tempThresholdLabel = thresholds
    ? `${thresholds.temp_warning_c}C / ${thresholds.temp_critical_c}C`
    : '--';
  const humidityThresholdLabel = thresholds
    ? `${thresholds.humidity_low_warning}%-${thresholds.humidity_high_warning}%`
    : '--';
  const forecastLabel = forecastLevel === 'N/A' ? '--' : forecastLevel;
  const depletionDaysLabel =
    activeForecast?.status === 'ok' && activeForecast.days_to_empty !== null
      ? `${activeForecast.days_to_empty.toFixed(1)} jours`
      : activeForecast?.status === 'stable_or_refilling'
      ? 'Stable'
      : '--';
  const depletionEtaLabel =
    activeForecast?.status === 'ok' && activeForecast.estimated_empty_at
      ? formatDateTime(activeForecast.estimated_empty_at)
      : '--';
  const depletionMessage = activeForecast?.message || 'Prediction indisponible';
  const depletionRiskMeta = getLevelMeta(activeForecast?.risk_level ?? 'Normal');

  useEffect(() => {
    if (!selectedProduct) {
      return;
    }

    let cancelled = false;

    const loadPrediction = async () => {
      try {
        const response = await axios.get(`${API_BASE}/prediction`, {
          params: {
            produit_id: selectedProduct,
            limit: 120
          }
        });

        if (cancelled) {
          return;
        }

        const forecast = normalizeDepletionForecast(response.data, selectedProduct, activeProductLabel);
        setForecastByProduct((prev) => ({
          ...prev,
          [forecast.produit_id]: forecast
        }));
      } catch (error) {
        console.error('Erreur prediction rupture:', error);
      }
    };

    loadPrediction();

    return () => {
      cancelled = true;
    };
  }, [selectedProduct]);

  const loadAlerts = async () => {
    try {
      const response = await axios.get(`${API_BASE}/alerts?limit=100`);
      const alerts = (Array.isArray(response.data) ? response.data : [])
        .map(normalizeAlert)
        .slice(0, MAX_ALERTS);
      setAlertHistory(alerts);
    } catch (error) {
      console.error('Erreur chargement alertes:', error);
      setNotice('Impossible de rafraichir les alertes.');
      setTimeout(() => setNotice(''), 2500);
    }
  };

  const acknowledgeAlert = async (alertId) => {
    try {
      await axios.post(`${API_BASE}/alerts/${alertId}/ack`);
      setAlertHistory((prev) =>
        prev.map((item) => (item.id === alertId ? { ...item, acknowledged: true } : item))
      );
    } catch (error) {
      console.error('Erreur acquittement alerte:', error);
      setNotice('Impossible d acquitter cette alerte.');
      setTimeout(() => setNotice(''), 2500);
    }
  };

  const saveActuatorThreshold = async () => {
    const threshold = Number(thresholdInput);
    if (Number.isNaN(threshold) || threshold < 0 || threshold > 100) {
      setNotice('Seuil humidite invalide: choisir une valeur de 0 a 100.');
      setTimeout(() => setNotice(''), 2500);
      return;
    }

    setIsSavingActuator(true);
    try {
      const response = await axios.post(`${API_BASE}/actuator/config`, {
        humidity_threshold_pct: threshold
      });
      const state = normalizeActuator(response.data);
      setActuator(state);
      setThresholdInput(String(state.humidity_threshold_pct));
      setNotice('Seuil humidite actionneur mis a jour.');
      setTimeout(() => setNotice(''), 1800);
    } catch (error) {
      console.error('Erreur mise a jour seuil actionneur:', error);
      setNotice('Impossible de mettre a jour le seuil actionneur.');
      setTimeout(() => setNotice(''), 2500);
    } finally {
      setIsSavingActuator(false);
    }
  };

  const switchActuatorMode = async (mode) => {
    setIsSavingActuator(true);
    try {
      const response = await axios.post(`${API_BASE}/actuator/config`, { mode });
      const state = normalizeActuator(response.data);
      setActuator(state);
      setThresholdInput(String(state.humidity_threshold_pct));
    } catch (error) {
      console.error('Erreur mode actionneur:', error);
      setNotice('Impossible de changer le mode actionneur.');
      setTimeout(() => setNotice(''), 2500);
    } finally {
      setIsSavingActuator(false);
    }
  };

  const sendActuatorCommand = async (command) => {
    setIsSavingActuator(true);
    try {
      const response = await axios.post(`${API_BASE}/actuator/command`, { command });
      const state = normalizeActuator(response.data);
      setActuator(state);
      setThresholdInput(String(state.humidity_threshold_pct));
      if (command === 'force_ventilation') {
        setNotice('Commande envoyee: ventilation forcee (relais ON, servo 90).');
        setTimeout(() => setNotice(''), 2200);
      }
    } catch (error) {
      console.error('Erreur commande actionneur:', error);
      setNotice('Impossible d envoyer la commande actionneur.');
      setTimeout(() => setNotice(''), 2500);
    } finally {
      setIsSavingActuator(false);
    }
  };

  return (
    <div className="min-h-screen px-4 py-6 sm:px-6 lg:px-10">
      <motion.header
        initial={{ opacity: 0, y: -20 }}
        animate={{ opacity: 1, y: 0 }}
        className="panel-glass mb-6 flex flex-col gap-4 p-5"
      >
        <div className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
          <div className="flex items-start gap-3">
            <div className="rounded-xl bg-sky-500/20 p-2 text-sky-300">
              <Boxes size={24} />
            </div>
            <div>
              <p className="font-display text-2xl font-semibold text-white sm:text-3xl">
                Smart Stock Influx Dashboard
              </p>
              <p className="text-sm text-muted">
                ESP32 Multi-capteurs + FastAPI + InfluxDB3 + Actionneur
              </p>
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-3">
            <div className={`rounded-full px-3 py-1 text-sm font-semibold ${levelMeta.badge}`}>
              Niveau: {levelMeta.label}
            </div>

            <div
              className={`flex items-center gap-2 rounded-full px-3 py-1 text-sm ${
                isConnected
                  ? 'border border-emerald-400/40 bg-emerald-500/15 text-emerald-200'
                  : 'border border-rose-400/40 bg-rose-500/15 text-rose-200'
              }`}
            >
              {isConnected ? <Wifi size={16} /> : <WifiOff size={16} />}
              <span>
                {isConnected
                  ? 'Connecte'
                  : reconnectDelayMs
                  ? `Reconnexion ${Math.round(reconnectDelayMs / 1000)}s`
                  : 'Deconnecte'}
              </span>
            </div>

            <button
              type="button"
              onClick={() => setIsDark((prev) => !prev)}
              className="rounded-xl border border-white/15 bg-white/10 p-2 text-slate-200 transition hover:bg-white/20"
              aria-label="Changer le theme"
            >
              {isDark ? <Sun size={18} /> : <MoonStar size={18} />}
            </button>
          </div>
        </div>

        <div className="flex flex-col gap-2 md:flex-row md:items-center md:gap-3">
          <label htmlFor="product-select" className="text-sm text-slate-300/90">
            Produit actif:
          </label>
          <select
            id="product-select"
            value={selectedProduct}
            onChange={(event) => setSelectedProduct(event.target.value)}
            className="rounded-xl border border-white/15 bg-slate-900/60 px-3 py-2 text-sm text-slate-100 outline-none transition focus:border-sky-400/60"
          >
            {products.length === 0 ? (
              <option value="">Aucun produit</option>
            ) : (
              products.map((item) => (
                <option key={item.produit_id} value={item.produit_id}>
                  {item.product} ({item.produit_id})
                </option>
              ))
            )}
          </select>
          <span className="text-xs text-slate-300/70">{filteredHistory.length} points affiches</span>
        </div>
      </motion.header>

      <AnimatePresence>
        {notice && (
          <motion.div
            initial={{ opacity: 0, y: -10 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -10 }}
            className="mb-5 rounded-xl border border-rose-400/40 bg-rose-500/20 px-4 py-3 text-sm text-rose-100"
          >
            {notice}
          </motion.div>
        )}
      </AnimatePresence>

      <AnimatePresence>
        {isCritical && (
          <motion.div
            initial={{ opacity: 0, scale: 0.98 }}
            animate={{ opacity: 1, scale: 1 }}
            exit={{ opacity: 0, scale: 0.98 }}
            className="mb-5 flex items-center gap-2 rounded-xl border border-rose-400/40 bg-rose-500/25 px-4 py-3 text-rose-100"
          >
            <TriangleAlert size={18} />
            <span>Crise critique detectee: stock et/ou environnement hors seuil.</span>
          </motion.div>
        )}
      </AnimatePresence>

      <section className="mb-6 grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <motion.article initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} className="panel-glass p-5">
          <p className="mb-2 text-sm text-muted">Produit</p>
          <div className="flex items-center justify-between gap-3">
            <p className="font-display text-2xl font-semibold text-white">{activeProductLabel}</p>
            <Cpu className="text-sky-300" size={26} />
          </div>
          <p className="mt-2 text-xs text-slate-300/75">ID: {selectedProduct || '--'}</p>
        </motion.article>

        <motion.article
          initial={{ opacity: 0, y: 16 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.05 }}
          className="panel-glass p-5"
        >
          <p className="mb-2 text-sm text-muted">Stock actuel</p>
          <div className="flex items-center justify-between">
            <p className="font-display text-4xl font-semibold text-white">{currentLevel}%</p>
            <Gauge className="text-sky-300" size={28} />
          </div>
        </motion.article>

        <motion.article
          initial={{ opacity: 0, y: 16 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.1 }}
          className="panel-glass p-5"
        >
          <p className="mb-2 text-sm text-muted">Distance (cm)</p>
          <div className="flex items-center justify-between">
            <p className="font-display text-4xl font-semibold text-white">{distanceCm}</p>
            <Ruler className="text-indigo-300" size={28} />
          </div>
        </motion.article>

        <motion.article
          initial={{ opacity: 0, y: 16 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.15 }}
          className="panel-glass p-5"
        >
          <p className="mb-2 text-sm text-muted">Statut stock</p>
          <div className="flex items-center justify-between">
            <span className={`rounded-full px-3 py-1 text-sm font-semibold ${statusMeta.badge}`}>
              {statusMeta.label}
            </span>
            <Boxes className="text-violet-300" size={26} />
          </div>
        </motion.article>

        <motion.article
          initial={{ opacity: 0, y: 16 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.2 }}
          className="panel-glass p-5"
        >
          <p className="mb-2 text-sm text-muted">Temperature</p>
          <div className="flex items-center justify-between">
            <p className="font-display text-3xl font-semibold text-white">{tempLabel}</p>
            <Thermometer className="text-orange-300" size={26} />
          </div>
          <p className="mt-2 text-xs text-slate-300/75">Seuils: {tempThresholdLabel}</p>
        </motion.article>

        <motion.article
          initial={{ opacity: 0, y: 16 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.25 }}
          className="panel-glass p-5"
        >
          <p className="mb-2 text-sm text-muted">Humidite</p>
          <div className="flex items-center justify-between">
            <p className="font-display text-3xl font-semibold text-white">{humidityLabel}</p>
            <Droplets className="text-cyan-300" size={26} />
          </div>
          <p className="mt-2 text-xs text-slate-300/75">Zone: {humidityThresholdLabel}</p>
        </motion.article>

        <motion.article
          initial={{ opacity: 0, y: 16 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.3 }}
          className="panel-glass p-5"
        >
          <p className="mb-2 text-sm text-muted">Prediction (T+1)</p>
          <p className="font-display text-4xl font-semibold text-white">
            {prediction === 'N/A' ? '--' : `${prediction}%`}
          </p>
          <p className="mt-2 text-xs text-slate-300/75">Niveau predit: {forecastLabel}</p>
        </motion.article>

        <motion.article
          initial={{ opacity: 0, y: 16 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.35 }}
          className="panel-glass p-5"
        >
          <p className="mb-2 text-sm text-muted">Score de risque</p>
          <div className="flex items-center justify-between">
            <p className="font-display text-3xl font-semibold text-white">{currentRiskScore}/100</p>
            <ShieldAlert className="text-amber-300" size={26} />
          </div>
          <p className="mt-2 text-xs text-slate-300/75">
            {currentRecommendation || 'Aucune recommandation active'}
          </p>
        </motion.article>

        <motion.article
          initial={{ opacity: 0, y: 16 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.4 }}
          className={`panel-glass p-5 ${depletionRiskMeta.panel}`}
        >
          <p className="mb-2 text-sm text-muted">Rupture estimee</p>
          <div className="flex items-center justify-between gap-2">
            <p className="font-display text-3xl font-semibold text-white">{depletionDaysLabel}</p>
            <TriangleAlert className="text-amber-300" size={24} />
          </div>
          <p className="mt-2 text-xs text-slate-100/90">Niveau: {depletionRiskMeta.label}</p>
          <p className="mt-1 text-xs text-slate-100/90">ETA: {depletionEtaLabel}</p>
          <p className="mt-1 text-xs text-slate-100/90">{depletionMessage}</p>
        </motion.article>
      </section>

      <section className="mb-6 panel-glass p-5">
        <div className="mb-4 flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
          <h2 className="font-display text-xl font-semibold text-white">Controle actionneur</h2>
          <div className="flex flex-wrap items-center gap-2">
            <span
              className={`rounded-full px-3 py-1 text-xs font-semibold ${
                actuator.state === 'on'
                  ? 'border border-emerald-400/40 bg-emerald-500/20 text-emerald-100'
                  : 'border border-slate-300/30 bg-slate-500/15 text-slate-200'
              }`}
            >
              Etat: {actuator.state.toUpperCase()}
            </span>
            <span className="rounded-full border border-sky-400/30 bg-sky-500/15 px-3 py-1 text-xs text-sky-200">
              Mode: {actuator.mode}
            </span>
            <span className="rounded-full border border-white/20 bg-slate-900/40 px-3 py-1 text-xs text-slate-100">
              Maj: {formatDate(actuator.updated_at)}
            </span>
          </div>
        </div>

        <div className="grid gap-4 lg:grid-cols-[1fr_1fr_1fr]">
          <div className="rounded-xl border border-white/10 bg-slate-900/45 p-4">
            <p className="mb-3 text-sm text-slate-300">Mode de pilotage</p>
            <div className="flex flex-wrap gap-2">
              <button
                type="button"
                disabled={isSavingActuator}
                onClick={() => switchActuatorMode('auto')}
                className="rounded-lg border border-white/20 bg-slate-900/50 px-3 py-1.5 text-sm text-slate-100 transition hover:bg-slate-800/65 disabled:opacity-60"
              >
                Auto
              </button>
              <button
                type="button"
                disabled={isSavingActuator}
                onClick={() => switchActuatorMode('manual')}
                className="rounded-lg border border-white/20 bg-slate-900/50 px-3 py-1.5 text-sm text-slate-100 transition hover:bg-slate-800/65 disabled:opacity-60"
              >
                Manuel
              </button>
            </div>
          </div>

          <div className="rounded-xl border border-white/10 bg-slate-900/45 p-4">
            <p className="mb-3 text-sm text-slate-300">Seuil humidite auto</p>
            <div className="flex items-center gap-2">
              <input
                type="number"
                min="0"
                max="100"
                value={thresholdInput}
                onChange={(event) => setThresholdInput(event.target.value)}
                className="w-24 rounded-lg border border-white/20 bg-slate-900/60 px-2 py-1.5 text-sm text-slate-100 outline-none focus:border-sky-400/60"
              />
              <button
                type="button"
                disabled={isSavingActuator}
                onClick={saveActuatorThreshold}
                className="inline-flex items-center gap-2 rounded-lg border border-white/20 bg-slate-900/50 px-3 py-1.5 text-sm text-slate-100 transition hover:bg-slate-800/65 disabled:opacity-60"
              >
                <SlidersHorizontal size={14} />
                Sauver
              </button>
            </div>
            <p className="mt-2 text-xs text-slate-300/80">Actif a {actuator.humidity_threshold_pct}%</p>
          </div>

          <div className="rounded-xl border border-white/10 bg-slate-900/45 p-4">
            <p className="mb-3 text-sm text-slate-300">Commande manuelle</p>
            <div className="flex flex-wrap gap-2">
              <button
                type="button"
                disabled={isSavingActuator}
                onClick={() => sendActuatorCommand('on')}
                className="inline-flex items-center gap-2 rounded-lg border border-emerald-400/30 bg-emerald-500/15 px-3 py-1.5 text-sm text-emerald-100 transition hover:bg-emerald-500/25 disabled:opacity-60"
              >
                <Power size={14} /> ON
              </button>
              <button
                type="button"
                disabled={isSavingActuator}
                onClick={() => sendActuatorCommand('force_ventilation')}
                className="inline-flex items-center gap-2 rounded-lg border border-cyan-300/40 bg-cyan-500/15 px-3 py-1.5 text-sm text-cyan-100 transition hover:bg-cyan-500/25 disabled:opacity-60"
              >
                <Power size={14} /> Forcer Ventilation
              </button>
              <button
                type="button"
                disabled={isSavingActuator}
                onClick={() => sendActuatorCommand('off')}
                className="inline-flex items-center gap-2 rounded-lg border border-rose-400/30 bg-rose-500/15 px-3 py-1.5 text-sm text-rose-100 transition hover:bg-rose-500/25 disabled:opacity-60"
              >
                <Power size={14} /> OFF
              </button>
              <button
                type="button"
                disabled={isSavingActuator}
                onClick={() => sendActuatorCommand('auto')}
                className="inline-flex items-center gap-2 rounded-lg border border-sky-400/30 bg-sky-500/15 px-3 py-1.5 text-sm text-sky-100 transition hover:bg-sky-500/25 disabled:opacity-60"
              >
                AUTO
              </button>
            </div>
          </div>
        </div>
      </section>

      <section className="grid gap-6 xl:grid-cols-[1.5fr_1fr]">
        <div className="panel-glass p-5">
          <div className="mb-4 flex items-center justify-between">
            <h2 className="font-display text-xl font-semibold text-white">Historique temps reel</h2>
            <span className="rounded-full border border-sky-300/30 bg-sky-500/15 px-2 py-1 text-xs text-sky-200">
              {filteredHistory.length} points
            </span>
          </div>

          <div className="h-80">
            <Line data={chartData} options={chartOptions} />
          </div>
        </div>

        <div className="grid gap-6">
          <div className="panel-glass p-5">
            <h2 className="mb-4 font-display text-xl font-semibold text-white">Historique des donnees</h2>
            <div className="max-h-80 overflow-auto rounded-xl border border-white/10">
              <table className="w-full border-collapse text-left text-sm">
                <thead className="sticky top-0 bg-slate-900/90">
                  <tr>
                    <th className="px-3 py-2 font-semibold text-slate-200">Date</th>
                    <th className="px-3 py-2 font-semibold text-slate-200">Produit</th>
                    <th className="px-3 py-2 font-semibold text-slate-200">Valeur</th>
                    <th className="px-3 py-2 font-semibold text-slate-200">Distance</th>
                    <th className="px-3 py-2 font-semibold text-slate-200">Act</th>
                  </tr>
                </thead>
                <tbody>
                  {tableRows.length === 0 ? (
                    <tr>
                      <td colSpan="5" className="px-3 py-6 text-center text-slate-300/70">
                        Aucune donnee disponible
                      </td>
                    </tr>
                  ) : (
                    tableRows.map((item) => (
                      <tr key={item.id} className="border-t border-white/10">
                        <td className="px-3 py-2 text-slate-200">{item.date}</td>
                        <td className="px-3 py-2 text-slate-200">{item.product}</td>
                        <td className="px-3 py-2 text-slate-100">{item.valeur}%</td>
                        <td className="px-3 py-2 text-slate-200">{item.distance} cm</td>
                        <td className="px-3 py-2 text-slate-200 uppercase">{item.actuator_state}</td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          </div>

          <div className="panel-glass p-5">
            <div className="mb-4 flex items-center justify-between gap-3">
              <h2 className="font-display text-xl font-semibold text-white">Historique des alertes</h2>
              <button
                type="button"
                onClick={loadAlerts}
                className="inline-flex items-center gap-2 rounded-xl border border-white/15 bg-white/10 px-3 py-1.5 text-sm text-slate-200 transition hover:bg-white/20"
              >
                <RefreshCw size={15} />
                Rafraichir
              </button>
            </div>

            <div className="max-h-96 overflow-auto space-y-2 pr-1">
              {alertRows.length === 0 ? (
                <div className="rounded-xl border border-white/10 bg-slate-900/45 px-4 py-6 text-center text-sm text-slate-300/80">
                  Aucune alerte pour le moment.
                </div>
              ) : (
                alertRows.map((alert) => (
                  <div key={alert.id} className={`rounded-xl px-3 py-3 ${getLevelMeta(alert.level).panel}`}>
                    <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
                      <div className="inline-flex items-center gap-2">
                        <BellRing size={16} />
                        <span className="text-sm font-semibold">
                          {alert.level} · {alert.alert_type}
                        </span>
                      </div>
                      <span className="text-xs text-slate-200/80">{formatDate(alert.created_at)}</span>
                    </div>

                    <p className="text-sm text-slate-100/95">
                      Produit: {alert.product} ({alert.produit_id}) · Stock: {alert.valeur}%
                    </p>

                    {(alert.temperature_c !== null || alert.humidity_pct !== null) && (
                      <p className="mt-1 text-xs text-slate-200/90">
                        Env: {alert.temperature_c === null ? '--' : `${alert.temperature_c.toFixed(1)}C`} /{' '}
                        {alert.humidity_pct === null ? '--' : `${alert.humidity_pct.toFixed(1)}%`}
                      </p>
                    )}

                    <p className="mt-1 text-xs text-slate-100/90">{alert.reasons || 'Aucune raison detaillee.'}</p>

                    {alert.recommendation && (
                      <p className="mt-1 text-xs text-slate-100/90">
                        Action recommandee: {alert.recommendation}
                      </p>
                    )}

                    <div className="mt-2 flex flex-wrap items-center gap-2">
                      <span className="rounded-full border border-white/20 px-2 py-0.5 text-xs text-slate-100">
                        Risque: {alert.risk_score}/100
                      </span>
                      <span className="rounded-full border border-white/20 px-2 py-0.5 text-xs text-slate-100">
                        Canaux: {alert.sent_channels.length ? alert.sent_channels.join(', ') : 'UI'}
                      </span>
                      {alert.notification_suppressed && (
                        <span className="rounded-full border border-amber-300/40 bg-amber-500/20 px-2 py-0.5 text-xs text-amber-100">
                          Cooldown actif
                        </span>
                      )}
                      <button
                        type="button"
                        disabled={alert.acknowledged}
                        onClick={() => acknowledgeAlert(alert.id)}
                        className="rounded-lg border border-white/20 bg-slate-900/40 px-2 py-1 text-xs text-slate-100 transition hover:bg-slate-800/60 disabled:cursor-not-allowed disabled:opacity-60"
                      >
                        {alert.acknowledged ? 'Acquittee' : 'Acquitter'}
                      </button>
                    </div>
                  </div>
                ))
              )}
            </div>
          </div>
        </div>
      </section>
    </div>
  );
}
