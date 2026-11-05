"""Data-feed engine with redundancy and health monitoring.

Implements the spec's feed requirements:
- Primary BTC feed (PolyBolt price.crypto, Chainlink)
- Secondary BTC feed (redundant PolyBolt connection)
- Independent Chainlink TWAP feed (price.crypto.twap, 60s)
- Health states: HEALTHY / DEGRADED / STALE / DOWN / UNVERIFIED
- Default failover: BLOCK (never silently switch providers)
- Never merge historical feature windows from different providers
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Callable, Optional

from polybot.adapters.polybolt import PolyBoltClient, PolyBoltConfig
from polybot.adapters.market_stream import MarketStreamClient, AssetSubscription
from polybot.clock import utc_now, utc_now_ts_ms
from polybot.constants import BTC_SYMBOL, TWAP_WINDOW_SECONDS
from polybot.decimal_math import dec, safe_divide
from polybot.types import BTCTick, TWAPTick, FeedHealth

logger = logging.getLogger("polybot.feeds")


@dataclass
class FeedConfig:
    primary_symbol: str = BTC_SYMBOL
    secondary_symbol: str = BTC_SYMBOL
    twap_symbol: str = BTC_SYMBOL
    twap_window_seconds: int = TWAP_WINDOW_SECONDS
    failover: str = "BLOCK"
    staleness_ms: int = 30_000
    degraded_ms: int = 10_000


@dataclass
class FeedHealthReport:
    primary: FeedHealth = FeedHealth.UNVERIFIED
    secondary: FeedHealth = FeedHealth.UNVERIFIED
    twap: FeedHealth = FeedHealth.UNVERIFIED
    overall: FeedHealth = FeedHealth.UNVERIFIED
    block_new_entries: bool = True
    reason: str = "feeds unverified"
    updated_at: Optional[datetime] = None


class FeedManager:
    """Orchestrates BTC price and Chainlink TWAP feeds with redundancy."""

    def __init__(
        self,
        *,
        api_key: str,
        secret: str,
        passphrase: str,
        config: Optional[FeedConfig] = None,
    ) -> None:
        self.config = config or FeedConfig()
        self.primary = PolyBoltClient(
            PolyBoltConfig(api_key=api_key, secret=secret, passphrase=passphrase)
        )
        # Secondary is a fully independent PolyBolt connection (redundant).
        self.secondary = PolyBoltClient(
            PolyBoltConfig(api_key=api_key, secret=secret, passphrase=passphrase)
        )
        # TWAP feed reuses the primary connection but on a distinct channel.
        self._market_stream: Optional[MarketStreamClient] = None

        self._primary_tick: Optional[BTCTick] = None
        self._secondary_tick: Optional[BTCTick] = None
        self._twap_tick: Optional[TWAPTick] = None

        self._tick_handlers: list[Callable[[BTCTick], None]] = []
        self._twap_handlers: list[Callable[[TWAPTick], None]] = []
        self._book_handlers: list[Callable] = []
        self._lock = asyncio.Lock()
        self._started = False
        self._last_health_report = FeedHealthReport()

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #
    async def start(self) -> None:
        if self._started:
            return
        await self.primary.connect()
        await self.secondary.connect()
        self.primary.add_handler(self._on_primary_tick)
        self.secondary.add_handler(self._on_secondary_tick)
        await self.primary.subscribe_price(self.config.primary_symbol)
        await self.primary.subscribe_twap(self.config.twap_symbol, self.config.twap_window_seconds)
        self._started = True
        # Health monitor task.
        asyncio.create_task(self._health_loop())

    async def stop(self) -> None:
        await self.primary.disconnect()
        await self.secondary.disconnect()
        if self._market_stream:
            await self._market_stream.disconnect()
        self._started = False

    def attach_market_stream(self, stream: MarketStreamClient) -> None:
        self._market_stream = stream
        stream.add_handler(self._on_book)

    # ------------------------------------------------------------------ #
    # Handlers
    # ------------------------------------------------------------------ #
    def on_tick(self, handler: Callable[[BTCTick], None]) -> None:
        self._tick_handlers.append(handler)

    def on_twap(self, handler: Callable[[TWAPTick], None]) -> None:
        self._twap_handlers.append(handler)

    def on_book(self, handler: Callable) -> None:
        self._book_handlers.append(handler)

    async def _on_primary_tick(self, envelope: dict) -> None:
        tick = envelope["tick"]
        if isinstance(tick, TWAPTick):
            # TWAP channel ticks arrive on the primary connection; keep them
            # separate so they never overwrite the spot price tick.
            self._set_twap(tick)
            return
        self._primary_tick = tick
        self._last_health_report.primary = self.primary.channel_health(
            "price.crypto", {"symbol": self.config.primary_symbol}
        )
        self._last_health_report.twap = self.primary.channel_health(
            "price.crypto.twap", {"symbol": self.config.twap_symbol, "window_seconds": self.config.twap_window_seconds}
        )
        for h in self._tick_handlers:
            try:
                h(tick)
            except Exception:
                logger.exception("tick handler error")

    async def _on_secondary_tick(self, envelope: dict) -> None:
        tick = envelope["tick"]
        self._secondary_tick = tick
        self._last_health_report.secondary = self.secondary.channel_health(
            "price.crypto", {"symbol": self.config.secondary_symbol}
        )
        for h in self._tick_handlers:
            try:
                h(tick)
            except Exception:
                logger.exception("tick handler error")

    async def _on_book(self, envelope: dict) -> None:
        for h in self._book_handlers:
            try:
                h(envelope)
            except Exception:
                logger.exception("book handler error")

    # ------------------------------------------------------------------ #
    # Current values
    # ------------------------------------------------------------------ #
    def primary_price(self) -> Optional[Decimal]:
        return self._primary_tick.price if self._primary_tick else None

    def secondary_price(self) -> Optional[Decimal]:
        return self._secondary_tick.price if self._secondary_tick else None

    def twap_value(self) -> Optional[Decimal]:
        return self._twap_tick.value if self._twap_tick else None

    def twap_tick(self) -> Optional[TWAPTick]:
        return self._twap_tick

    def latest_tick(self) -> Optional[BTCTick]:
        """Return the freshest primary tick (never the secondary for decisions)."""
        return self._primary_tick

    # ------------------------------------------------------------------ #
    # Health
    # ------------------------------------------------------------------ #
    def health(self) -> FeedHealthReport:
        p = self._last_health_report.primary
        s = self._last_health_report.secondary
        t = self._last_health_report.twap
        block = True
        reason = "feeds unverified"
        overall = FeedHealth.UNVERIFIED

        if p == FeedHealth.DOWN and s == FeedHealth.DOWN and t == FeedHealth.DOWN:
            overall = FeedHealth.DOWN
            block = True
            reason = "all feeds down"
        elif p == FeedHealth.DOWN:
            if self.config.failover == "BLOCK":
                overall = FeedHealth.DEGRADED
                block = True
                reason = "primary feed DOWN; failover BLOCKED"
            else:
                overall = FeedHealth.DEGRADED
                block = False
                reason = "primary feed DOWN; using secondary"
        elif p in (FeedHealth.STALE, FeedHealth.UNVERIFIED):
            overall = FeedHealth.STALE
            block = True
            reason = "primary feed stale or unverified"
        elif p == FeedHealth.DEGRADED:
            overall = FeedHealth.DEGRADED
            block = False
            reason = "primary feed degraded"
        else:
            overall = FeedHealth.HEALTHY
            block = False
            reason = "feeds healthy"

        self._last_health_report = FeedHealthReport(
            primary=p,
            secondary=s,
            twap=t,
            overall=overall,
            block_new_entries=block,
            reason=reason,
            updated_at=utc_now(),
        )
        return self._last_health_report

    def can_enter(self) -> tuple[bool, str]:
        """Whether new entries are allowed given feed health and failover."""
        report = self.health()
        if report.block_new_entries:
            return False, report.reason
        if report.overall in (FeedHealth.DOWN, FeedHealth.UNVERIFIED):
            return False, report.reason
        return True, "feeds healthy"

    def staleness_ms(self, tick) -> Optional[int]:
        if tick is None or tick.data_age_ms is None:
            return None
        return tick.data_age_ms

    async def _health_loop(self) -> None:
        while self._started:
            try:
                self.health()
            except Exception:
                logger.exception("health loop error")
            await asyncio.sleep(2)

    # ------------------------------------------------------------------ #
    # TWAP handling (set from primary channel)
    # ------------------------------------------------------------------ #
    def _set_twap(self, tick: TWAPTick) -> None:
        self._twap_tick = tick
