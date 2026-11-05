"""CLOB market-stream websocket client.

Connects to ``wss://ws-subscriptions-clob.polymarket.com/ws/market`` to receive
live order-book snapshots, price changes, last-trade prices and tick-size
changes for subscribed outcome asset IDs.

Per the official docs the connection uses an application-level heartbeat: send
text ``PING`` every 10 seconds and expect ``PONG``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Callable, Optional

import websockets

from polybot.clock import parse_iso, utc_now
from polybot.constants import CLOB_MARKET_WS_URL
from polybot.decimal_math import dec, qshare
from polybot.types import OrderBookSnapshot, FeedHealth

logger = logging.getLogger("polybot.market_stream")


@dataclass
class MarketStreamConfig:
    url: str = CLOB_MARKET_WS_URL
    ping_interval: float = 10.0
    reconnect_base_delay: float = 2.0
    max_reconnect_delay: float = 60.0


@dataclass
class AssetSubscription:
    asset_id: str
    condition_id: Optional[str] = None


class MarketStreamClient:
    def __init__(self, config: Optional[MarketStreamConfig] = None) -> None:
        self.config = config or MarketStreamConfig()
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._subscriptions: dict[str, AssetSubscription] = {}
        self._handlers: list[Callable[[dict], None]] = []
        self._lock = asyncio.Lock()
        self._reader_task: Optional[asyncio.Task] = None
        self._ping_task: Optional[asyncio.Task] = None
        self._stop = asyncio.Event()
        self._connect_lock = asyncio.Lock()
        self._books: dict[str, OrderBookSnapshot] = {}
        self._book_health: dict[str, FeedHealth] = {}

    # ------------------------------------------------------------------ #
    # Connection
    # ------------------------------------------------------------------ #
    async def connect(self) -> None:
        async with self._connect_lock:
            if self._ws is not None:
                return
            logger.info("Connecting to market stream %s", self.config.url)
            self._ws = await websockets.connect(self.config.url, ping_interval=None)
            self._stop.clear()
            self._reader_task = asyncio.create_task(self._read_loop())
            self._ping_task = asyncio.create_task(self._ping_loop())
            if self._subscriptions:
                await self._send_subscription()

    async def _ping_loop(self) -> None:
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.config.ping_interval)
                return
            except asyncio.TimeoutError:
                pass
            if self._ws and not self._ws.closed:
                try:
                    await self._ws.send("PING")
                except Exception:
                    logger.warning("Market stream ping failed; reconnecting")
                    await self.reconnect()
                    return

    async def _read_loop(self) -> None:
        assert self._ws is not None
        try:
            async for message in self._ws:
                await self._handle_message(message)
        except websockets.ConnectionClosed:
            logger.warning("Market stream connection closed")
        except Exception as exc:  # pragma: no cover
            logger.exception("Market stream read error: %s", exc)
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
                logger.warning("Market stream reconnect failed: %s", exc)
                delay = min(delay * 2, self.config.max_reconnect_delay)

    async def disconnect(self) -> None:
        self._stop.set()
        if self._reader_task:
            self._reader_task.cancel()
        if self._ping_task:
            self._ping_task.cancel()
        if self._ws and not self._ws.closed:
            try:
                await self._ws.close()
            except Exception:
                pass
        self._ws = None

    # ------------------------------------------------------------------ #
    # Subscriptions
    # ------------------------------------------------------------------ #
    def add_handler(self, handler: Callable[[dict], None]) -> None:
        self._handlers.append(handler)

    async def subscribe(self, assets: list[AssetSubscription]) -> None:
        for a in assets:
            self._subscriptions[a.asset_id] = a
        if self._ws and not self._ws.closed:
            await self._send_subscription()

    async def unsubscribe(self, asset_ids: list[str]) -> None:
        for aid in asset_ids:
            self._subscriptions.pop(aid, None)
        if self._ws and not self._ws.closed:
            await self._send_subscription()

    async def _send_subscription(self) -> None:
        assert self._ws is not None
        frame = {
            "assets_ids": list(self._subscriptions.keys()),
            "type": "market",
            "custom_feature_enabled": True,
        }
        await self._ws.send(json.dumps(frame))

    # ------------------------------------------------------------------ #
    # Message handling
    # ------------------------------------------------------------------ #
    async def _handle_message(self, message: str) -> None:
        if message == "PONG":
            return
        try:
            data = json.loads(message)
        except json.JSONDecodeError:
            return

        event_type = data.get("event_type")
        asset_id = data.get("asset_id") or data.get("market")
        ts = data.get("timestamp")
        dt = parse_iso(ts) if ts else utc_now()
        now_ms = int(time.time() * 1000)

        if event_type == "book" and asset_id:
            bids = [
                (dec(b.get("price")), dec(b.get("size")))
                for b in data.get("bids", [])
            ]
            asks = [
                (dec(a.get("price")), dec(a.get("size")))
                for a in data.get("asks", [])
            ]
            best_bid = bids[0][0] if bids else None
            best_ask = asks[0][0] if asks else None
            spread = (best_ask - best_bid) if (best_bid is not None and best_ask is not None) else None
            snap = OrderBookSnapshot(
                timestamp=dt,
                asset_id=asset_id,
                bids=bids,
                asks=asks,
                best_bid=best_bid,
                best_ask=best_ask,
                spread=spread,
                min_order_size=dec(data.get("minOrderSize")),
                tick_size=dec(data.get("tickSize")),
                hash=data.get("hash"),
                raw=data,
            )
            self._books[asset_id] = snap
            self._book_health[asset_id] = FeedHealth.HEALTHY
            for handler in self._handlers:
                try:
                    handler({"snapshot": snap, "raw": data})
                except Exception:  # pragma: no cover
                    logger.exception("Handler error")
            return

        if event_type == "best_bid_ask" and asset_id:
            snap = self._books.get(asset_id)
            if snap is None:
                snap = OrderBookSnapshot(timestamp=dt, asset_id=asset_id)
            snap.best_bid = dec(data.get("best_bid")) if data.get("best_bid") else snap.best_bid
            snap.best_ask = dec(data.get("best_ask")) if data.get("best_ask") else snap.best_ask
            snap.spread = dec(data.get("spread")) if data.get("spread") else snap.spread
            snap.timestamp = dt
            self._books[asset_id] = snap
            self._book_health[asset_id] = FeedHealth.HEALTHY
            for handler in self._handlers:
                try:
                    handler({"snapshot": snap, "raw": data})
                except Exception:  # pragma: no cover
                    logger.exception("Handler error")
            return

        if event_type == "tick_size_change" and asset_id:
            snap = self._books.get(asset_id)
            if snap is None:
                snap = OrderBookSnapshot(timestamp=dt, asset_id=asset_id)
            snap.tick_size = dec(data.get("new_tick_size")) if data.get("new_tick_size") else snap.tick_size
            snap.timestamp = dt
            self._books[asset_id] = snap
            for handler in self._handlers:
                try:
                    handler({"tick_size_change": snap, "raw": data})
                except Exception:  # pragma: no cover
                    logger.exception("Handler error")

        # Track data age / health.
        if asset_id:
            snap = self._books.get(asset_id)
            if snap is not None and snap.timestamp:
                age_ms = now_ms - int(snap.timestamp.timestamp() * 1000)
                self._book_health[asset_id] = (
                    FeedHealth.STALE if age_ms > 30_000 else FeedHealth.HEALTHY
                )

        for handler in self._handlers:
            try:
                handler({"event": data})
            except Exception:  # pragma: no cover
                logger.exception("Handler error")

    # ------------------------------------------------------------------ #
    # Queries
    # ------------------------------------------------------------------ #
    def book(self, asset_id: str) -> Optional[OrderBookSnapshot]:
        return self._books.get(asset_id)

    def book_health(self, asset_id: str) -> FeedHealth:
        return self._book_health.get(asset_id, FeedHealth.UNVERIFIED)

    def is_connected(self) -> bool:
        return self._ws is not None and not self._ws.closed
