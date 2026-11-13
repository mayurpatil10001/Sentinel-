import os

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.models import Base

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./sentinel_dev.db")

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def init_db():
    Base.metadata.create_all(bind=engine)
    _safe_migrate_columns()


def _safe_migrate_columns():
    """Ensure newly added columns exist in older SQLite database files."""
    from sqlalchemy import inspect, text

    inspector = inspect(engine)
    existing_tables = inspector.get_table_names()

    with engine.connect() as conn:
        if "instruments" in existing_tables:
            inst_cols = {col["name"] for col in inspector.get_columns("instruments")}
            if "source" not in inst_cols:
                conn.execute(text("ALTER TABLE instruments ADD COLUMN source VARCHAR(32) DEFAULT 'synthetic_demo'"))
                conn.commit()

        if "alerts" in existing_tables:
            alert_cols = {col["name"] for col in inspector.get_columns("alerts")}
            if "event_date" not in alert_cols:
                # Add column; SQLite cannot add NOT NULL without a default.
                # server_default="2000-01-01" handles new DB creation; existing
                # DBs get the column with NULL then we backfill below.
                conn.execute(text("ALTER TABLE alerts ADD COLUMN event_date DATE DEFAULT '2000-01-01'"))
            # Backfill any NULLs: derive event_date from window_start (UTC date).
            # This is correct for IST-era data; the small UTC/IST difference
            # (5h30m) only matters for alerts generated near midnight IST.
            conn.execute(text(
                "UPDATE alerts SET event_date = date(window_start) "
                "WHERE event_date IS NULL"
            ))
            if "last_seen_at" not in alert_cols:
                conn.execute(text("ALTER TABLE alerts ADD COLUMN last_seen_at DATETIME"))
            if "occurrences" not in alert_cols:
                conn.execute(text("ALTER TABLE alerts ADD COLUMN occurrences INTEGER DEFAULT 1"))
            if "source" not in alert_cols:
                conn.execute(text("ALTER TABLE alerts ADD COLUMN source VARCHAR(32) DEFAULT 'synthetic_demo'"))
            if "ml_anomaly_score" not in alert_cols:
                conn.execute(text("ALTER TABLE alerts ADD COLUMN ml_anomaly_score FLOAT"))
            if "score_method" not in alert_cols:
                conn.execute(text("ALTER TABLE alerts ADD COLUMN score_method VARCHAR(32)"))
            conn.commit()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
