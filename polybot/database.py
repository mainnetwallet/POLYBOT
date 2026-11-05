"""Trading-state database (SQLite WAL).

POLYBOT stores all persistent trading state in SQLite with WAL journaling:
orders journal, positions, market state machines, risk state, exposure
reservations, resolution results, heartbeats, and decision audit log.

Analytics / market history use Parquet + DuckDB (see data/store.py).
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional

from polybot.clock import utc_now

SCHEMA_VERSION = 1

# ---------------------------------------------------------------------------
# Schema (all migrations are additive and idempotent)
# ---------------------------------------------------------------------------
SCHEMA_SQL = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
PRAGMA foreign_keys=ON;

-- Discovered markets and their persistent state machine.
CREATE TABLE IF NOT EXISTS markets (
    market_id           TEXT PRIMARY KEY,
    slug                TEXT,
    question            TEXT,
    condition_id        TEXT,
    version             TEXT NOT NULL,
    state               TEXT NOT NULL DEFAULT 'DISCOVERED',
    active              INTEGER DEFAULT 0,
    closed              INTEGER DEFAULT 0,
    accepting_orders    INTEGER DEFAULT 0,
    start_date          TEXT,
    end_date            TEXT,
    event_start_time    TEXT,
    min_tick_size       TEXT,
    min_order_size      TEXT,
    fees_enabled        INTEGER DEFAULT 0,
    liquidity           TEXT,
    last_seen           TEXT,
    raw_json            TEXT
);

-- Market data / tick snapshots (recent window in SQLite; long history in Parquet).
CREATE TABLE IF NOT EXISTS market_data (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    market_id           TEXT NOT NULL,
    timestamp           TEXT NOT NULL,
    btc_price           TEXT,
    btc_source          TEXT,
    twap                TEXT,
    twap_window_seconds INTEGER,
    up_price            TEXT,
    down_price          TEXT,
    best_bid            TEXT,
    best_ask            TEXT,
    spread              TEXT,
    liquidity           TEXT,
    source              TEXT DEFAULT 'polymarket',
    quality_flags       TEXT DEFAULT '[]',
    FOREIGN KEY (market_id) REFERENCES markets(market_id)
);
CREATE INDEX IF NOT EXISTS idx_market_data_market_ts ON market_data(market_id, timestamp);

-- Order journal (persisted BEFORE any order is submitted - disaster recovery).
CREATE TABLE IF NOT EXISTS orders (
    idempotency_key     TEXT PRIMARY KEY,
    market_id           TEXT NOT NULL,
    condition_id        TEXT,
    asset_id            TEXT NOT NULL,
    side                TEXT NOT NULL,
    price               TEXT NOT NULL,
    size_shares         TEXT NOT NULL,
    order_type          TEXT NOT NULL,
    strategy_version    TEXT NOT NULL,
    timestamp           TEXT NOT NULL,
    status              TEXT NOT NULL DEFAULT 'PENDING',
    order_id            TEXT,
    filled_shares       TEXT DEFAULT '0',
    rejection_reason    TEXT,
    reconciliation_notes TEXT,
    FOREIGN KEY (market_id) REFERENCES markets(market_id)
);
CREATE INDEX IF NOT EXISTS idx_orders_market ON orders(market_id);
CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status);

-- Filled positions.
CREATE TABLE IF NOT EXISTS positions (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    market_id           TEXT NOT NULL,
    condition_id        TEXT,
    asset_id            TEXT NOT NULL,
    side                TEXT NOT NULL,
    entry_price         TEXT NOT NULL,
    quantity_shares     TEXT NOT NULL,
    cost_usd            TEXT NOT NULL,
    current_price       TEXT,
    unrealized_pnl_usd  TEXT,
    exit_state          TEXT NOT NULL DEFAULT 'OPEN',
    dust                INTEGER DEFAULT 0,
    residual_shares     TEXT,
    entry_order_id      TEXT,
    filled_at           TEXT,
    resolved_at         TEXT,
    raw_json            TEXT,
    FOREIGN KEY (market_id) REFERENCES markets(market_id)
);
CREATE INDEX IF NOT EXISTS idx_positions_asset ON positions(asset_id);

-- Risk / exposure state (persisted across restarts).
CREATE TABLE IF NOT EXISTS risk_state (
    id                  INTEGER PRIMARY KEY CHECK (id = 1),
    date                TEXT NOT NULL,
    daily_pnl_usd       TEXT DEFAULT '0',
    daily_loss_usd      TEXT DEFAULT '0',
    consecutive_losses  INTEGER DEFAULT 0,
    cooldown_until      TEXT,
    available_balance_usd TEXT DEFAULT '0',
    allocated_capital_usd TEXT DEFAULT '0',
    reserved_capital_usd TEXT DEFAULT '0',
    exposure_usd        TEXT DEFAULT '0',
    updated_at          TEXT NOT NULL
);

-- Exposure reservations (capital reserved before order submission).
CREATE TABLE IF NOT EXISTS exposure_reservations (
    reservation_id      TEXT PRIMARY KEY,
    market_id           TEXT NOT NULL,
    asset_id            TEXT NOT NULL,
    side                TEXT NOT NULL,
    reserved_usd        TEXT NOT NULL,
    created_at          TEXT NOT NULL,
    released_at         TEXT,
    status              TEXT NOT NULL DEFAULT 'ACTIVE'
);
CREATE INDEX IF NOT EXISTS idx_exposure_reservations_active ON exposure_reservations(status);

-- Resolution simulation results.
CREATE TABLE IF NOT EXISTS resolution_results (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    market_id           TEXT NOT NULL,
    rules_fingerprint   TEXT,
    outcome             TEXT,
    confidence          TEXT,
    validation_result   TEXT,
    opening_reference   TEXT,
    closing_reference   TEXT,
    timestamp           TEXT NOT NULL,
    raw_json            TEXT
);

-- Heartbeat state.
CREATE TABLE IF NOT EXISTS heartbeat_state (
    id                  INTEGER PRIMARY KEY CHECK (id = 1),
    latest_id           TEXT,
    last_sent           TEXT,
    cadence_seconds     INTEGER DEFAULT 5,
    enabled             INTEGER DEFAULT 0,
    healthy             INTEGER DEFAULT 0,
    failures            INTEGER DEFAULT 0,
    updated_at          TEXT NOT NULL
);

-- Live gate state.
CREATE TABLE IF NOT EXISTS live_gate_state (
    id                  INTEGER PRIMARY KEY CHECK (id = 1),
    enabled             INTEGER DEFAULT 0,
    activation_reason   TEXT,
    last_check          TEXT,
    conditions_json     TEXT DEFAULT '[]',
    updated_at          TEXT NOT NULL
);

-- Decision audit log.
CREATE TABLE IF NOT EXISTS audit_log (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp           TEXT NOT NULL,
    component           TEXT NOT NULL,
    action              TEXT NOT NULL,
    market_id           TEXT,
    detail_json         TEXT DEFAULT '{}',
    level               TEXT DEFAULT 'INFO'
);
CREATE INDEX IF NOT EXISTS idx_audit_component_ts ON audit_log(component, timestamp);

-- Database meta.
CREATE TABLE IF NOT EXISTS db_meta (
    key                 TEXT PRIMARY KEY,
    value               TEXT NOT NULL
);
"""

# ---------------------------------------------------------------------------
# Database connection manager
# ---------------------------------------------------------------------------


class Database:
    """Thread-safe SQLite WAL database for POLYBOT trading state."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._lock = threading.Lock()
        self._initialized = False

    def _conn(self) -> sqlite3.Connection:
        if not hasattr(self._local, "connection") or self._local.connection is None:
            conn = sqlite3.connect(str(self.db_path), check_same_thread=False, timeout=30.0)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA synchronous=NORMAL;")
            conn.execute("PRAGMA foreign_keys=ON;")
            self._local.connection = conn
        return self._local.connection

    def close(self) -> None:
        conn = getattr(self._local, "connection", None)
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
            self._local.connection = None

    def initialize(self) -> None:
        with self._lock:
            conn = self._conn()
            conn.executescript(SCHEMA_SQL)
            conn.commit()
            # Record schema version.
            conn.execute(
                "INSERT OR REPLACE INTO db_meta(key, value) VALUES (?, ?)",
                ("schema_version", str(SCHEMA_VERSION)),
            )
            conn.commit()
            self._initialized = True

    # -- execute helpers ---------------------------------------------------

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        conn = self._conn()
        cur = conn.execute(sql, params)
        conn.commit()
        return cur

    def executemany(self, sql: str, params: list[tuple]) -> sqlite3.Cursor:
        conn = self._conn()
        cur = conn.executemany(sql, params)
        conn.commit()
        return cur

    def fetchone(self, sql: str, params: tuple = ()) -> Optional[sqlite3.Row]:
        conn = self._conn()
        return conn.execute(sql, params).fetchone()

    def fetchall(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        conn = self._conn()
        return conn.execute(sql, params).fetchall()

    def integrity_check(self) -> bool:
        """Run PRAGMA integrity_check and quick_check."""
        try:
            res = self.fetchone("PRAGMA quick_check;")
            return res is not None and str(res[0]).lower() == "ok"
        except Exception:
            return False

    def vacuum(self) -> None:
        self.execute("VACUUM;")
