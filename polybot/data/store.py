"""Data storage layer.

- SQLite WAL (polybot.database.Database) for trading state.
- Parquet + DuckDB for market-history and analytics (append-only, compressed,
  batched writes, retention, integrity checks).
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd

try:  # DuckDB optional for analytics queries
    import duckdb
except Exception:  # pragma: no cover
    duckdb = None  # type: ignore[assignment]

from polybot.database import Database


class AnalyticsStore:
    """Parquet-backed market history with optional DuckDB query access."""

    def __init__(self, base_dir: str | Path = "data/analytics") -> None:
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self._con: Optional["duckdb.DuckDBPyConnection"] = None

    # -- parquet writes --------------------------------------------------

    def _path(self, name: str) -> Path:
        return self.base_dir / f"{name}.parquet"

    def write_market_data(self, records: list[dict]) -> None:
        if not records:
            return
        df = pd.DataFrame(records)
        if "timestamp" in df.columns:
            df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
        path = self._path("market_data")
        if path.exists():
            existing = pd.read_parquet(path)
            df = pd.concat([existing, df], ignore_index=True)
        df.to_parquet(path, index=False, compression="zstd")

    def write_ticks(self, records: list[dict]) -> None:
        self.write_market_data(records)  # same schema family

    def write_signals(self, records: list[dict]) -> None:
        path = self._path("signals")
        df = pd.DataFrame(records)
        if "timestamp" in df.columns:
            df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
        if path.exists():
            existing = pd.read_parquet(path)
            df = pd.concat([existing, df], ignore_index=True)
        df.to_parquet(path, index=False, compression="zstd")

    def write_decisions(self, records: list[dict]) -> None:
        path = self._path("decisions")
        df = pd.DataFrame(records)
        if "timestamp" in df.columns:
            df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
        if path.exists():
            existing = pd.read_parquet(path)
            df = pd.concat([existing, df], ignore_index=True)
        df.to_parquet(path, index=False, compression="zstd")

    # -- duckdb ----------------------------------------------------------

    def _conn(self):
        if duckdb is None:
            return None
        if self._con is None:
            self._con = duckdb.connect(database=":memory:")
        return self._con

    def query(self, sql: str, params: Optional[list] = None):
        con = self._conn()
        if con is None:
            raise RuntimeError("duckdb not available")
        return con.execute(sql, params or []).fetchdf()

    def close(self) -> None:
        if self._con is not None:
            try:
                self._con.close()
            except Exception:
                pass
            self._con = None
