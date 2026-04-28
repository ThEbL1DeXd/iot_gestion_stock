CREATE DATABASE IF NOT EXISTS smart_stock;
USE smart_stock;

CREATE TABLE IF NOT EXISTS stock (
    id INT AUTO_INCREMENT PRIMARY KEY,
    valeur INT NOT NULL,
    product VARCHAR(120) NOT NULL DEFAULT 'Produit principal',
    device_id VARCHAR(80) NOT NULL DEFAULT 'esp32-default',
    temperature_c FLOAT NULL,
    humidity_pct FLOAT NULL,
    date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS alerts (
    id INT AUTO_INCREMENT PRIMARY KEY,
    alert_type VARCHAR(80) NOT NULL,
    level VARCHAR(20) NOT NULL,
    product VARCHAR(120) NOT NULL,
    valeur INT NOT NULL,
    temperature_c FLOAT NULL,
    humidity_pct FLOAT NULL,
    reasons TEXT NOT NULL,
    recommendation TEXT NOT NULL,
    risk_score INT NOT NULL DEFAULT 0,
    fingerprint VARCHAR(180) NOT NULL,
    cooldown_until TIMESTAMP NULL,
    sent_channels VARCHAR(255) NOT NULL DEFAULT '',
    notification_suppressed BOOLEAN NOT NULL DEFAULT FALSE,
    acknowledged BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_alert_level (level),
    INDEX idx_alert_product (product),
    INDEX idx_alert_created_at (created_at),
    INDEX idx_alert_fingerprint (fingerprint)
);
