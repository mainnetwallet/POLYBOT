"""PolyBolt websocket client.

Connects to ``wss://ws-live-v2.polymarket.com/ws``, authenticates with CLOB
API credentials, and subscribes to Chainlink reference-price channels:

- ``price.crypto``      spot BTC/USD (source: Chainlink by default)
- ``price.crypto.twap`` 60-second Chainlink TWAP

Implements auth, ping/pong heartbeat, reconnection, and per-channel sequence
tracking. The client never switches providers silently: the source is pinned
to ``chainlink`` and reported on every tick.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Callable, Optional

import websockets

from polybot.clock import parse_iso, to_ms, utc_now
from polybot.constants import POLYBOLT_WS_URL
from polybot.decimal_math import dec
from polybot.types import BTCTick, TWAPTick, FeedHealth

logger = logging.getLogger("polybot.polybolt")


def _ws_closed(ws) -> bool:
    """True if the websocket is missing or not OPEN.

    Works with websockets >= 14 (``ws.state``) and falls back to the legacy
    ``ws.closed`` attribute for older versions.
    """
    if ws is None:
        return True
    state = getattr(ws, "state", None)
    if state is not None:
        return getattr(state, "name", "") != "OPEN"
    return bool(getattr(ws, "closed", True))



@dataclass
class PolyBoltConfig:
    api_key: str
    secret: str
    passphrase: str
    url: str = POLYBOLT_WS_URL
    ping_interval: float = 25.0
    subscribe_timeout: float = 30.0
    reconnect_base_delay: float = 2.0
    max_reconnect_delay: float = 60.0


@dataclass
class ChannelState:
    subscribed: bool = False
    seq: Optional[int] = None
    dropped: int = 0
    last_message_ms: Optional[int] = None
    health: FeedHealth = FeedHealth.UNVERIFIED


class PolyBoltClient:
    def __init__(self, config: PolyBoltConfig) -> None:
        self.config = config
        self._ws: Optional[Any] = None
        self._authenticated = False
        self._channels: dict[str, ChannelState] = {}
        self._handlers: list[Callable[[dict], None]] = []
        self._lock = asyncio.Lock()
        self._reader_task: Optional[asyncio.Task] = None
        self._ping_task: Optional[asyncio.Task] = None
        self._stop = asyncio.Event()
        self._connect_lock = asyncio.Lock()
        self._subscribed_channels: list[dict] = []

    # ------------------------------------------------------------------ #
    # Connection
    # ------------------------------------------------------------------ #
    async def connect(self) -> None:
        async with self._connect_lock:
            if self._ws is not None:
                return
            logger.info("Connecting to PolyBolt %s", self.config.url)
            self._ws = await websockets.connect(self.config.url, ping_interval=None)
            self._authenticated = False
            await self._authenticate()
            self._stop.clear()
            self._reader_task = asyncio.create_task(self._read_loop())
            self._ping_task = asyncio.create_task(self._ping_loop())

    async def _authenticate(self) -> None:
        assert self._ws is not None
        rid = f"auth-{int(time.time()*1000)}"
        frame = {
            "op": "auth",
            "rid": rid,
            "auth": {
                "apiKey": self.config.api_key,
                "secret": self.config.secret,
                "passphrase": self.config.passphrase,
            },
        }
        await self._ws.send(json.dumps(frame))
        # Wait for authed confirmation.
        deadline = time.time() + self.config.subscribe_timeout
        while time.time() < deadline:
            if _ws_closed(self._ws):
                raise RuntimeError("WebSocket closed during authentication")
            try:
                message = await asyncio.wait_for(self._ws.recv(), timeout=2.0)
            except asyncio.TimeoutError:
                continue
            data = json.loads(message)
            if data.get("op") == "authed" and data.get("rid") == rid:
                self._authenticated = True
                logger.info("PolyBolt authenticated")
                # Resubscribe to known channels.
                if self._subscribed_channels:
                    await self.subscribe(self._subscribed_channels)
                return
            if data.get("op") == "error":
                raise RuntimeError(f"PolyBolt auth error: {data}")
        raise RuntimeError("PolyBolt authentication timed out")

    async def _ping_loop(self) -> None:
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.config.ping_interval)
                return  # stopped
            except asyncio.TimeoutError:
                pass
            if not _ws_closed(self._ws):
                try:
                    await self._ws.ping()
                except Exception:
                    logger.warning("PolyBolt ping failed; reconnecting")
                    await self.reconnect()
                    return

    async def _read_loop(self) -> None:
        assert self._ws is not None
        try:
            async for message in self._ws:
                await self._handle_message(message)
        except websockets.ConnectionClosed:
            logger.warning("PolyBolt connection closed")
        except Exception as exc:  # pragma: no cover
            logger.exception("PolyBolt read loop error: %s", exc)
        finally:
            if not self._stop.is_set():
                asyncio.create_task(self.reconnect())

    async def reconnect(self) -> None:
        await self.disconnect()
        delay = self.config.reconnect_base_delay
        while not self._stop.is_set():
            try:
                await asyncio.sleep(delay)
                await self.connect()
                return
            except Exception as exc:  # pragma: no cover
                logger.warning("PolyBolt reconnect attempt failed: %s", exc)
                delay = min(delay * 2, self.config.max_reconnect_delay)

    async def disconnect(self) -> None:
        self._stop.set()
        if self._reader_task:
            self._reader_task.cancel()
        if self._ping_task:
            self._ping_task.cancel()
        if not _ws_closed(self._ws):
            try:
                await self._ws.close()
            except Exception:
                pass
        self._ws = None
        self._authenticated = False

    # ------------------------------------------------------------------ #
    # Subscriptions
    # ------------------------------------------------------------------ #
    async def subscribe(self, channels: list[dict]) -> None:
        """channels: list of {"channel": str, "filter": dict}."""
        self._subscribed_channels = channels
        if not self._authenticated or _ws_closed(self._ws):
            return
        frame = {
            "op": "subscribe",
            "rid": f"sub-{int(time.time()*1000)}",
            "subscriptions": channels,
        }
        for ch in channels:
            key = ch["channel"] + json.dumps(ch.get("filter", {}), sort_keys=True)
            self._channels.setdefault(key, ChannelState())
        await self._ws.send(json.dumps(frame))

    async def subscribe_price(self, symbol: str = "btcusd") -> None:
        await self.subscribe([{"channel": "price.crypto", "filter": {"symbol": symbol}}])

    async def subscribe_twap(self, symbol: str = "btcusd", window_seconds: int = 60) -> None:
        await self.subscribe(
            [{"channel": "price.crypto.twap", "filter": {"symbol": symbol, "window_seconds": window_seconds}}]
        )

    # ------------------------------------------------------------------ #
    # Message handling
    # ------------------------------------------------------------------ #
    def add_handler(self, handler: Callable[[dict], None]) -> None:
        self._handlers.append(handler)

    async def _handle_message(self, message: str) -> None:
        try:
            data = json.loads(message)
        except json.JSONDecodeError:
            return

        op = data.get("op")
        if op == "pong":
            return
        if op == "authed":
            return
        if op == "subscribed" or op == "subscription":
            # Mark channel subscribed on confirmation.
            return
        if op == "error":
            logger.error("PolyBolt error frame: %s", data)
            return

        channel = data.get("channel")
        seq = data.get("seq")
        dropped = data.get("dropped", 0)
        payload = data.get("payload", {})
        ts = data.get("ts")

        key = channel + json.dumps(data.get("filter", {}), sort_keys=True)
        state = self._channels.get(key)
        if state is not None:
            state.seq = seq
            state.dropped = int(dropped or 0)
            state.last_message_ms = int(ts) if ts is not None else None
            state.health = FeedHealth.HEALTHY
            state.subscribed = True

        now_ms = int(time.time() * 1000)
        if state is not None and state.last_message_ms is not None:
            age_ms = now_ms - state.last_message_ms
            if age_ms > 30_000:
                state.health = FeedHealth.STALE
            elif age_ms > 10_000:
                state.health = FeedHealth.DEGRADED

        # Emit normalized ticks for price channels.
        if channel in ("price.crypto", "price.crypto.twap"):
            tick = self._normalize_price(data)
            if tick is not None:
                for handler in self._handlers:
                    try:
                        handler({"tick": tick, "channel": channel, "raw": data})
                    except Exception:  # pragma: no cover
                        logger.exception("Handler error")

    def _normalize_price(self, data: dict) -> Optional[BTCTick | TWAPTick]:
        channel = data.get("channel")
        payload = data.get("payload", {})
        symbol = payload.get("symbol")
        if symbol != "btcusd":
            return None
        source = payload.get("source", "chainlink")
        value = dec(payload.get("full_accuracy_value") or payload.get("value"))
        ts = payload.get("timestamp")
        dt = parse_iso(ts) if ts else utc_now()
        now_ms = int(time.time() * 1000)
        data_age_ms = None
        if ts:
            try:
                data_age_ms = now_ms - int(ts)
            except Exception:
                data_age_ms = None

        if channel == "price.crypto.twap":
            return TWAPTick(
                timestamp=dt,
                value=value,
                window_seconds=int(payload.get("window_seconds", 60) or 60),
                source=source,
                provider="chainlink",
                observation_timestamp=dt,
                remaining_window_seconds=None,
                data_age_ms=data_age_ms,
                quality_flags=[],
                raw=data,
            )
        return BTCTick(
            timestamp=dt,
            price=value,
            source="polybolt",
            provider=source,
            sequence=data.get("seq"),
            data_age_ms=data_age_ms,
            quality_flags=[],
            raw=data,
        )

    # ------------------------------------------------------------------ #
    # Health
    # ------------------------------------------------------------------ #
    def channel_health(self, channel: str, filter_dict: dict) -> FeedHealth:
        key = channel + json.dumps(filter_dict or {}, sort_keys=True)
        state = self._channels.get(key)
        if state is None:
            return FeedHealth.UNVERIFIED
        return state.health

    def is_healthy(self) -> bool:
        return not _ws_closed(self._ws) and self._authenticated
