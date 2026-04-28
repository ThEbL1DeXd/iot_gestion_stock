from sqlalchemy import (
    create_engine,
    Column,
    Integer,
    Float,
    DateTime,
    String,
    Boolean,
    Text,
    inspect,
    text,
)
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from datetime import datetime
from pathlib import Path
import os

# URL de connexion principale (MySQL), configurable par variable d'environnement.
DEFAULT_MYSQL_DATABASE_URL = "mysql+pymysql://root:@localhost/smart_stock"
SQLALCHEMY_DATABASE_URL = os.getenv("SMART_STOCK_DB_URL", DEFAULT_MYSQL_DATABASE_URL)


def _sqlite_fallback_url() -> str:
    db_path = Path(__file__).resolve().with_name("smart_stock_fallback.db")
    return f"sqlite:///{db_path.as_posix()}"

engine = create_engine(SQLALCHEMY_DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
DATABASE_READY = False
DATABASE_BACKEND = "mysql"

Base = declarative_base()

class Stock(Base):
    __tablename__ = "stock"

    id = Column(Integer, primary_key=True, index=True)
    valeur = Column(Integer, nullable=False)
    product = Column(String(120), nullable=False, default="Produit principal")
    device_id = Column(String(80), nullable=False, default="esp32-default")
    temperature_c = Column(Float, nullable=True)
    humidity_pct = Column(Float, nullable=True)
    date = Column(DateTime, default=datetime.utcnow)


class Alert(Base):
    __tablename__ = "alerts"

    id = Column(Integer, primary_key=True, index=True)
    alert_type = Column(String(80), nullable=False)
    level = Column(String(20), nullable=False)
    product = Column(String(120), nullable=False)
    valeur = Column(Integer, nullable=False)
    temperature_c = Column(Float, nullable=True)
    humidity_pct = Column(Float, nullable=True)
    reasons = Column(Text, nullable=False, default="")
    recommendation = Column(Text, nullable=False, default="")
    risk_score = Column(Integer, nullable=False, default=0)
    fingerprint = Column(String(180), nullable=False, index=True)
    cooldown_until = Column(DateTime, nullable=True)
    sent_channels = Column(String(255), nullable=False, default="")
    notification_suppressed = Column(Boolean, nullable=False, default=False)
    acknowledged = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)


def ensure_schema_upgrades() -> None:
    inspector = inspect(engine)

    if inspector.has_table("stock"):
        columns = {column["name"] for column in inspector.get_columns("stock")}
        statements = []

        if "product" not in columns:
            statements.append(
                "ALTER TABLE stock ADD COLUMN product VARCHAR(120) NOT NULL DEFAULT 'Produit principal'"
            )
        if "device_id" not in columns:
            statements.append(
                "ALTER TABLE stock ADD COLUMN device_id VARCHAR(80) NOT NULL DEFAULT 'esp32-default'"
            )
        if "temperature_c" not in columns:
            statements.append("ALTER TABLE stock ADD COLUMN temperature_c FLOAT NULL")
        if "humidity_pct" not in columns:
            statements.append("ALTER TABLE stock ADD COLUMN humidity_pct FLOAT NULL")

        if statements:
            with engine.begin() as connection:
                for statement in statements:
                    connection.execute(text(statement))

    if inspector.has_table("alerts"):
        columns = {column["name"] for column in inspector.get_columns("alerts")}
        statements = []

        if "notification_suppressed" not in columns:
            statements.append(
                "ALTER TABLE alerts ADD COLUMN notification_suppressed INTEGER NOT NULL DEFAULT 0"
            )
        if "recommendation" not in columns:
            statements.append(
                "ALTER TABLE alerts ADD COLUMN recommendation TEXT NOT NULL DEFAULT ''"
            )
        if "risk_score" not in columns:
            statements.append(
                "ALTER TABLE alerts ADD COLUMN risk_score INTEGER NOT NULL DEFAULT 0"
            )

        if statements:
            with engine.begin() as connection:
                for statement in statements:
                    connection.execute(text(statement))


def _initialize_schema() -> None:
    Base.metadata.create_all(bind=engine)
    ensure_schema_upgrades()


def _switch_to_sqlite_fallback() -> None:
    global engine, DATABASE_BACKEND
    fallback_engine = create_engine(
        _sqlite_fallback_url(),
        connect_args={"check_same_thread": False},
        pool_pre_ping=True,
    )
    engine = fallback_engine
    SessionLocal.configure(bind=fallback_engine)
    DATABASE_BACKEND = "sqlite_fallback"


def initialize_database() -> None:
    global DATABASE_READY

    try:
        _initialize_schema()
        DATABASE_READY = True
        return
    except SQLAlchemyError:
        DATABASE_READY = False

    # Fallback automatique pour garder l'API disponible si MySQL est indisponible.
    if not SQLALCHEMY_DATABASE_URL.startswith("sqlite"):
        try:
            _switch_to_sqlite_fallback()
            _initialize_schema()
            DATABASE_READY = True
            return
        except SQLAlchemyError:
            DATABASE_READY = False


def is_database_available() -> bool:
    return DATABASE_READY


initialize_database()
