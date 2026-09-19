"""
=============================================================
  CAMPUS DIGITAL TWIN v2 — DB LAYER (SQLite + SQLModel)
=============================================================

Persistence contract
--------------------
- One `OccupancyRecord` row per facility per telemetry tick.
- The database is THE system of record: on restart the backend
  re-seeds current state from the newest row per facility.
- Writes are batched/serialized through a single background
  writer to keep SQLite happy under concurrent ticks.

Backend selection (for free-tier cloud deploys)
-----------------------------------------------
- Default (no env var): local SQLite at ./data/campus.db — zero setup.
- CAMPUS_TWIN_DATABASE_URL / DATABASE_URL set: any SQLAlchemy URL.
  On Render + Supabase, paste the Supabase connection string
  (Session Pooler URI, port 5432) and the twin persists to Postgres
  so history survives redeploys (Render free disks are ephemeral).
"""

from __future__ import annotations

import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, List, Optional

from sqlmodel import Field, Session, SQLModel, create_engine, select

logger = logging.getLogger("campus_twin.db")

# ── Database location ────────────────────────────────────────
# 1. Explicit env var wins (CAMPUS_TWIN_DATABASE_URL, then DATABASE_URL —
#    the latter is what most PaaS providers set for managed databases).
# 2. Otherwise: local SQLite at <project>/data/campus.db (the default).

# Supabase/Render hand out 'postgresql://' URIs; SQLAlchemy ≥2.0 wants
# the 'postgresql+psycopg2://' driver-qualified form. Rewrite transparently.
def _normalize_url(url: str) -> str:
    if url.startswith("postgres://"):
        return "postgresql+psycopg2://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        return "postgresql+psycopg2://" + url[len("postgresql://"):]
    return url


DATABASE_URL = os.environ.get(
    "CAMPUS_TWIN_DATABASE_URL", os.environ.get("DATABASE_URL", "")
)

if DATABASE_URL:
    DATABASE_URL = _normalize_url(DATABASE_URL)
    IS_SQLITE = DATABASE_URL.startswith("sqlite")
else:
    DB_DIR = Path(__file__).resolve().parent.parent / "data"
    DB_FILE = DB_DIR / "campus.db"
    DATABASE_URL = f"sqlite:///{DB_FILE}"
    IS_SQLITE = True

# ── Engine tuning ────────────────────────────────────────────
# SQLite: check_same_thread=False lets the engine be shared across the
#   uvicorn event loop + simulator threads; every access still goes
#   through short-lived Sessions and the write lock below.
# Postgres (Supabase): no special args, but a conservative pool size
#   fits the free-tier connection limits comfortably.
_engine_kwargs: dict = {"echo": False}
if IS_SQLITE:
    _engine_kwargs["connect_args"] = {"check_same_thread": False}
else:
    _engine_kwargs.update(pool_size=5, max_overflow=5, pool_pre_ping=True)

engine = create_engine(DATABASE_URL, **_engine_kwargs)


# ─────────────────────────────────────────────────────────────
#  TABLE MODEL
# ─────────────────────────────────────────────────────────────

class OccupancyRecord(SQLModel, table=True):
    """
    Immutable historical telemetry row.

    One row is appended every time a facility reports occupancy.
    Predictive analytics queries aggregate this table with a
    moving-average window to forecast capacity breaches.
    """
    __tablename__ = "occupancy_history"

    id: Optional[int] = Field(default=None, primary_key=True)
    facility_id: str = Field(index=True, description="Facility identifier, e.g. 'science-block'")
    occupancy: int = Field(description="Headcount / vehicle count at tick time")
    occupancy_pct: float = Field(description="occupancy / capacity * 100")
    capacity: int = Field(description="Max capacity of the facility at tick time")
    # Index on ts makes the analytics window query (last 15 min) a cheap scan.
    ts: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        index=True,
        description="UTC timestamp of the tick",
    )


# ─────────────────────────────────────────────────────────────
#  SCHEMA INIT + WRITE SERIALIZATION
# ─────────────────────────────────────────────────────────────

_init_lock = threading.Lock()
_initialized = False

# SQLite locks the whole file on write. A coarse process-wide lock
# serializes commits from the simulator thread(s) + API requests;
# at demo tick rates (~6 writes/sec) contention is negligible.
_write_lock = threading.Lock()


def init_db() -> None:
    """Create data dir + tables once. Safe to call repeatedly."""
    global _initialized
    with _init_lock:
        if _initialized:
            return
        if IS_SQLITE:
            DB_DIR.mkdir(parents=True, exist_ok=True)
        SQLModel.metadata.create_all(engine)
        _initialized = True
        # Log only the host part — never the user:password@ credentials.
        safe_target = DATABASE_URL.split("@")[-1] if "@" in DATABASE_URL else DATABASE_URL
        logger.info("🗄️  Database ready (%s): %s",
                    "sqlite" if IS_SQLITE else "postgres", safe_target)


def insert_records(records: Iterable[OccupancyRecord]) -> int:
    """
    Batch-insert telemetry rows. Returns the number of rows written.
    Serialized with _write_lock so concurrent threads never interleave
    two write transactions on the same SQLite file.
    """
    rows = list(records)
    if not rows:
        return 0
    with _write_lock:
        with Session(engine) as session:
            session.add_all(rows)
            session.commit()
    return len(rows)


def insert_record(record: OccupancyRecord) -> None:
    """Single-row convenience wrapper around insert_records()."""
    insert_records([record])


def latest_record_per_facility() -> dict[str, OccupancyRecord]:
    """
    Return the newest OccupancyRecord for every facility.

    Used at boot to restore the twin to its last-known state instead
    of a cold synthetic baseline — one of the "production-grade"
    behaviours this build advertises.
    """
    latest: dict[str, OccupancyRecord] = {}
    with Session(engine) as session:
        statement = select(OccupancyRecord).order_by(
            OccupancyRecord.facility_id, OccupancyRecord.ts
        )
        for row in session.exec(statement):
            latest[row.facility_id] = row   # later ts overwrites earlier
    return latest


def history_for_facility(
    facility_id: str,
    window_minutes: int = 60,
    limit: int = 120,
) -> List[OccupancyRecord]:
    """
    Historical rows for one facility, oldest → newest, restricted to
    the trailing `window_minutes`. Used by the Chart.js sidebar graph.
    """
    cutoff = datetime.now(timezone.utc).timestamp() - window_minutes * 60
    cutoff_dt = datetime.fromtimestamp(cutoff, tz=timezone.utc)

    with Session(engine) as session:
        statement = (
            select(OccupancyRecord)
            .where(OccupancyRecord.facility_id == facility_id)
            .where(OccupancyRecord.ts >= cutoff_dt)
            .order_by(OccupancyRecord.ts.desc())
            .limit(limit)
        )
        rows = list(session.exec(statement))
    rows.reverse()   # oldest → newest for time-series plotting

    # SQLite stores naive-UTC strings; re-attach UTC so downstream
    # epoch math / ISO serialization can never misread them as local time.
    for r in rows:
        if r.ts is not None and r.ts.tzinfo is None:
            r.ts = r.ts.replace(tzinfo=timezone.utc)
    return rows
