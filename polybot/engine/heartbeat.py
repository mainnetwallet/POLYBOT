"""Heartbeat engine.

Manages the Polymarket CLOB heartbeat (POST /v1/heartbeats). Keeps open orders
alive. Critical safety rules:

- Dedicated API key exclusively for POLYBOT.
- Never send a production heartbeat during development.
- Never activate until LIVE conditions pass.
- Heartbeat cancellation may affect ALL open orders across markets/processes.
- Heartbeat worker stops when the main process becomes unhealthy.
- Heartbeat does NOT close filled positions.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

from polybot.clock import utc_now
from polybot.constants import MODE
from polybot.types import HeartbeatState

logger = logging.getLogger("polybot.heartbeat")


@dataclass
class HeartbeatConfig:
    enabled: bool = False
    cadence_seconds: float = 5.0
    timeout_seconds: float = 15.0
    api_key: Optional[str] = None
    api_secret: Optional[str] = None
    passphrase: Optional[str] = None


class HeartbeatEngine:
    def __init__(self, db, config: HeartbeatConfig, mode: str = MODE.PAPER) -> None:
        self.db = db
        self.config = config
        self.mode = mode
        self.state = HeartbeatState(
            enabled=config.enabled,
            cadence_seconds=int(config.cadence_seconds),
        )
        self._task: Optional[asyncio.Task] = None
        self._stop = asyncio.Event()
        self._healthy = True  # main-process health, set externally
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #
    async def start(self, require_live: bool = True) -> None:
        if self.mode != MODE.LIVE and require_live:
            logger.warning("Heartbeat start refused: not in LIVE mode")
            return
        if not self.config.enabled:
            logger.info("Heartbeat disabled by configuration")
            return
        if not self.config.api_key:
            logger.warning("Heartbeat start refused: no dedicated API key")
            return
        if self._task is not None:
            return
        self._stop.clear()
        self.state.enabled = True
        self.state.healthy = True
        self._task = asyncio.create_task(self._run())
        logger.info("Heartbeat engine started (cadence %ss)", self.config.cadence_seconds)

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        self.state.enabled = False
        self.state.healthy = False
        logger.info("Heartbeat engine stopped")

    def set_process_healthy(self, healthy: bool) -> None:
        self._healthy = healthy
        if not healthy:
            # Heartbeat worker must stop when main process is unhealthy.
            asyncio.create_task(self.stop())

    # ------------------------------------------------------------------ #
    # Worker
    # ------------------------------------------------------------------ #
    async def _run(self) -> None:
        while not self._stop.is_set():
            if not self._healthy:
                logger.warning("Heartbeat pausing: main process unhealthy")
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=1.0)
                except asyncio.TimeoutError:
                    pass
                continue
            await self._send_heartbeat()
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.config.cadence_seconds)
            except asyncio.TimeoutError:
                pass

    async def _send_heartbeat(self) -> None:
        now = utc_now()
        # Restore latest ID from DB.
        row = self.db.fetchone("SELECT latest_id FROM heartbeat_state WHERE id = 1")
        if row and row["latest_id"]:
            self.state.latest_id = row["latest_id"]

        try:
            hb_id = await self._post_heartbeat(self.state.latest_id)
            self.state.latest_id = hb_id
            self.state.last_sent = now
            self.state.failures = 0
            self.state.healthy = True
            await self._persist()
            logger.debug("Heartbeat sent: %s", short_id(hb_id))
        except Exception as exc:  # pragma: no cover - network
            self.state.failures += 1
            self.state.healthy = self.state.failures < 3
            logger.warning("Heartbeat failure (%d): %s", self.state.failures, exc)
            # Treat heartbeat-triggered cancellation as a normal recovery event.

    async def _post_heartbeat(self, previous_id: Optional[str]) -> str:
        """Send a heartbeat to the CLOB. Returns the new heartbeat ID.

        Implementation calls the CLOB REST heartbeat endpoint. In PAPER mode
        or without credentials it returns a simulated ID.
        """
        if self.mode != MODE.LIVE or not self.config.api_key:
            sim = f"hb-paper-{int(time.time()*1000)}"
            return sim
        # Real implementation: POST to CLOB /v1/heartbeats with L2 auth.
        # (Delegate to the exchange adapter / clob_rest client.)
        raise NotImplementedError("Live heartbeat endpoint not wired to adapter")

    async def _persist(self) -> None:
        try:
            self.db.execute(
                """
                INSERT INTO heartbeat_state(id, latest_id, last_sent, cadence_seconds, enabled, healthy, failures, updated_at)
                VALUES (1,?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET
                    latest_id=excluded.latest_id, last_sent=excluded.last_sent,
                    cadence_seconds=excluded.cadence_seconds,
                    enabled=excluded.enabled, healthy=excluded.healthy,
                    failures=excluded.failures, updated_at=excluded.updated_at
                """,
                (
                    self.state.latest_id,
                    self.state.last_sent.isoformat() if self.state.last_sent else None,
                    self.state.cadence_seconds,
                    1 if self.state.enabled else 0,
                    1 if self.state.healthy else 0,
                    self.state.failures,
                    utc_now().isoformat(),
                ),
            )
        except Exception:
            logger.exception("Failed to persist heartbeat state")

    # ------------------------------------------------------------------ #
    # Recovery
    # ------------------------------------------------------------------ #
    async def restore(self) -> None:
        row = self.db.fetchone("SELECT * FROM heartbeat_state WHERE id = 1")
        if row:
            self.state.latest_id = row["latest_id"]
            self.state.last_sent = __import__("polybot.clock", fromlist=["parse_iso"]).parse_iso(row["last_sent"])
            self.state.enabled = bool(row["enabled"])
            self.state.healthy = bool(row["healthy"])
            self.state.failures = int(row["failures"] or 0)
        logger.info("Heartbeat state restored: latest_id=%s enabled=%s", short_id(self.state.latest_id), self.state.enabled)

    def status(self) -> dict:
        return {
            "enabled": self.state.enabled,
            "healthy": self.state.healthy,
            "latest_id": short_id(self.state.latest_id),
            "last_sent": self.state.last_sent.isoformat() if self.state.last_sent else None,
            "cadence_seconds": self.state.cadence_seconds,
            "failures": self.state.failures,
        }


def short_id(value: Optional[str]) -> str:
    if not value:
        return "-"
    return value[-8:]
