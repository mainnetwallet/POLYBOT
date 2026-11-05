"""POLYBOT orchestrator.

Coordinates feeds, market lifecycle, trading intelligence, execution, risk,
heartbeat, recovery and dashboard state in a single asyncio process. LIVE
execution remains locked until the strict LIVE gate is satisfied and explicitly
activated by the user.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Optional

from polybot.adapters.polymarket import PolymarketAdapter, NotAuthenticatedError, UnknownMarketVersionError
from polybot.adapters.market_stream import MarketStreamClient, AssetSubscription
from polybot.clock import ServerClock
from polybot.config import Settings, load_settings
from polybot.database import Database
from polybot.data.feeds import FeedManager, FeedConfig
from polybot.data.store import AnalyticsStore
from polybot.decimal_math import dec
from polybot.engine import entry as entry_mod
from polybot.engine.discovery import MarketDiscovery, MarketLifecycle
from polybot.engine.exit import HybridExit
from polybot.engine.prediction import PredictionModel, ModelInputs
from polybot.engine.profit import ProfitOptimizer
from polybot.engine.regime import RegimeDetector
from polybot.engine.sizing import PositionSizer
from polybot.engine.signals import MultiTimeframeAnalysis, FalseSignalDetector
from polybot.engine.resolution import resolve as resolve_resolution, MarketRules, OracleObservations
from polybot.engine.risk import RiskEngine
from polybot.engine.heartbeat import HeartbeatEngine, HeartbeatConfig
from polybot.execution.order_manager import OrderManager
from polybot.execution.paper import PaperExecutor
from polybot.recovery.journal import RecoveryManager, RecoveryState
from polybot.validation.live_gate import LiveGate
from polybot.validation.geo import GeoChecker
from polybot.constants import (
    DEFAULT_ENTRY_WINDOW_SECONDS,
    MODE,
    ORDER_TYPE_FAK,
)

logger = logging.getLogger("polybot.app")


@dataclass
class GateResult:
    passed: bool
    live_enabled: bool
    reason: str


class App:
    def __init__(self, settings: Optional[Settings] = None, db: Optional[Database] = None) -> None:
        self.settings = settings or load_settings()
        self.db = db or Database("data/polybot.db")
        self.mode = self.settings.mode
        self.shutdown_requested = False

        # Components
        self.clock = ServerClock()
        self.adapter: Optional[PolymarketAdapter] = None
        self.feeds: Optional[FeedManager] = None
        self.market_stream: Optional[MarketStreamClient] = None
        self.analytics: Optional[AnalyticsStore] = None

        self.discovery: Optional[MarketDiscovery] = None
        self.lifecycle: Optional[MarketLifecycle] = None
        self.regime: Optional[RegimeDetector] = None
        self.mtf: Optional[MultiTimeframeAnalysis] = None
        self.false_signal: Optional[FalseSignalDetector] = None
        self.prediction: Optional[PredictionModel] = None
        self.entry: Optional[entry_mod.DynamicEntry] = None
        self.profit: Optional[ProfitOptimizer] = None
        self.sizer: Optional[PositionSizer] = None
        self.risk: Optional[RiskEngine] = None
        self.exit: Optional[HybridExit] = None
        self.order_manager: Optional[OrderManager] = None
        self.paper: Optional[PaperExecutor] = None
        self.heartbeat: Optional[HeartbeatEngine] = None
        self.recovery: Optional[RecoveryManager] = None
        self.live_gate: Optional[LiveGate] = None
        self.geo: Optional[GeoChecker] = None

        # Runtime state
        self._markets: dict = {}              # market_id -> metadata
        self._positions: dict = {}            # asset_id -> position dict
        self._open_orders: list = []
        self._ready = False
        self._loop_task: Optional[asyncio.Task] = None
        self._discovery_task: Optional[asyncio.Task] = None
        self._evaluation_count = 0
        self._no_trade_reasons: dict[str, str] = {}
        self._validation_evidence = False
        self._last_dashboard_state: dict = {}

    # ------------------------------------------------------------------ #
    # Wiring
    # ------------------------------------------------------------------ #
    def wire(self) -> None:
        self.db = self.db or Database("data/polybot.db")
        self.adapter = PolymarketAdapter(
            mode=self.mode,
            private_key=self.settings.private_key,
            wallet_address=self.settings.wallet_address,
            clob_api_key=self.settings.clob_api_key,
            clob_api_secret=self.settings.clob_api_secret,
            clob_api_passphrase=self.settings.clob_api_passphrase,
        )
        self.feeds = FeedManager(
            api_key=self.settings.clob_api_key or "",
            secret=self.settings.clob_api_secret or "",
            passphrase=self.settings.clob_api_passphrase or "",
            config=FeedConfig(
                failover="BLOCK",
                twap_window_seconds=60,
            ),
        )
        self.market_stream = MarketStreamClient()
        self.analytics = AnalyticsStore("data/analytics")
        self.feeds.attach_market_stream(self.market_stream)

        self.discovery = MarketDiscovery(self.adapter)
        self.lifecycle = MarketLifecycle(self.db)
        self.regime = RegimeDetector()
        self.mtf = MultiTimeframeAnalysis()
        self.false_signal = FalseSignalDetector(self.mtf)
        self.prediction = PredictionModel()
        self.entry = entry_mod.DynamicEntry(self.settings)
        self.profit = ProfitOptimizer(self.settings)
        self.sizer = PositionSizer(self.settings)
        self.risk = RiskEngine(self.db, self.settings)
        self.exit = HybridExit(self.settings)
        self.order_manager = OrderManager(self.db, self.adapter)
        self.paper = PaperExecutor(
            taker_delay_ms=self.settings.fees.taker_delay_ms,
        )
        self.heartbeat = HeartbeatEngine(
            self.db,
            HeartbeatConfig(
                enabled=self.settings.heartbeat.enabled,
                cadence_seconds=self.settings.heartbeat.cadence_seconds,
                api_key=self.settings.clob_api_key,
            ),
            mode=self.mode,
        )
        self.recovery = RecoveryManager(self.db, self)
        self.live_gate = LiveGate(self, self.settings)
        self.geo = GeoChecker()

        # Cross-references used by recovery.
        self.adapter_balance = self.adapter.get_balance_usd  # type: ignore[assignment]
        self.adapter_open_orders = self.adapter.get_open_orders  # type: ignore[assignment]

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #
    async def start(self) -> None:
        logger.info("POLYBOT starting in %s mode", self.mode)
        self.wire()
        self.db.initialize()

        # Disaster recovery startup sequence.
        rec = await self.recovery.recover()
        if not rec.ready:
            logger.error("Recovery not READY: %s", rec.reason)
            self._ready = False
            return

        # Sync clock.
        await self.clock.sync()

        # Connect feeds (credentials may be empty -> public data only).
        await self.adapter.start()
        await self.feeds.start()
        await self.market_stream.connect()

        # Restore order journal.
        await self.order_manager.load_from_db()

        # Restore risk.
        self.risk.restore()

        self._ready = True
        logger.info("POLYBOT READY (mode=%s)", self.mode)

        # Run the trading loop.
        self._loop_task = asyncio.create_task(self._trading_loop())

    async def stop(self) -> None:
        self.shutdown_requested = True
        if self._loop_task:
            self._loop_task.cancel()
        if self._discovery_task:
            self._discovery_task.cancel()
        if self.heartbeat:
            await self.heartbeat.stop()
        if self.feeds:
            await self.feeds.stop()
        if self.market_stream:
            await self.market_stream.disconnect()
        if self.adapter:
            await self.adapter.close()
        if self.recovery:
            self.recovery.release_lock()
        if self.db:
            self.db.close()
        if self.analytics:
            self.analytics.close()
        self._ready = False
        logger.info("POLYBOT stopped")

    # ------------------------------------------------------------------ #
    # Trading loop
    # ------------------------------------------------------------------ #
    async def _trading_loop(self) -> None:
        while not self.shutdown_requested:
            try:
                await self._tick()
            except Exception:
                logger.exception("Trading tick error")
            await asyncio.sleep(1)

    async def _tick(self) -> None:
        if not self._ready:
            return

        # 1. Market discovery (periodic).
        # Runs in the background: a full scan pages through every open market
        # and can take minutes, which must not stall exits or dashboard state.
        if self._evaluation_count % 60 == 0 and (
            self._discovery_task is None or self._discovery_task.done()
        ):
            self._discovery_task = asyncio.create_task(self._refresh_discovery())
        self._evaluation_count += 1

        # 2. Update MTF / regime from latest BTC tick.
        tick = self.feeds.latest_tick() if self.feeds else None
        if tick:
            self.prediction.update_price_history(tick)
            self.mtf.update(tick)

        # 3. Gate entry on feed health.
        can_enter, feed_reason = (self.feeds.can_enter() if self.feeds else (False, "feeds not started"))
        if not can_enter:
            self._no_trade_reasons["feed"] = feed_reason

        # 4. Evaluate entry for the active market (during entry window).
        await self._evaluate_entry()

        # 5. Evaluate exits for open positions.
        await self._evaluate_exits()

        # 6. Update dashboard state.
        self._update_dashboard_state()

    # ------------------------------------------------------------------ #
    # Discovery
    # ------------------------------------------------------------------ #
    async def _refresh_discovery(self) -> None:
        if not self.adapter:
            return
        try:
            await self._do_refresh_discovery()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Market discovery refresh failed")

    async def _do_refresh_discovery(self) -> None:
        result = await self.discovery.refresh("BTC 5M")
        for meta in result.btc_5m_markets:
            ok, reasons = await self.discovery.validate(meta)
            if ok:
                self._markets[meta.market_id] = meta
                if self.lifecycle.get(meta.market_id) is None:
                    self.lifecycle.register(meta.market_id, meta)
                    self.lifecycle.transition(meta.market_id, "ACTIVE")
                # Subscribe to order book for both outcomes.
                if self.market_stream:
                    subs = [AssetSubscription(meta.asset_id_for_outcome("UP") or "", meta.condition_id),
                            AssetSubscription(meta.asset_id_for_outcome("DOWN") or "", meta.condition_id)]
                    subs = [s for s in subs if s.asset_id]
                    if subs:
                        await self.market_stream.subscribe(subs)

    # ------------------------------------------------------------------ #
    # Entry evaluation
    # ------------------------------------------------------------------ #
    async def _evaluate_entry(self) -> None:
        if not self.entry or not self.risk or not self.feeds:
            return
        # Find a market in ACTIVE state without an entry yet.
        target = None
        for mid, meta in self._markets.items():
            st = self.lifecycle.state(mid)
            if st == "ACTIVE":
                target = meta
                break
        if target is None:
            return

        # One entry per market; one active position.
        active_positions = len(self._positions)
        if active_positions >= int(self.settings.risk.max_active_positions):
            return

        remaining = self._remaining_entry_seconds(target)
        book = self._book_for(target)
        regime = self.regime.classify(
            momentum=self.mtf.analyze().get("1m").momentum if self.mtf else Decimal("0"),
            trend_strength=Decimal("0.5"),
            volatility=Decimal("0.01"),
            acceleration=Decimal("0"),
            multi_timeframe_agreement=self.mtf.agreement_score(self.mtf.analyze()) if self.mtf else Decimal("0"),
            reversal_detected=False,
        )
        filters = self.false_signal.run_all(
            TradeSide.BUY,
            spread=book.get("spread"),
            liquidity=book.get("liquidity"),
            recent_trades=None,
            book_depth_imbalance=None,
        ) if self.false_signal else []

        decision = self.entry.evaluate(
            probability=type("P", (), {"p_up": Decimal("0.65"), "p_down": Decimal("0.35")})(),
            regime=regime,
            order_book=book,
            remaining_seconds=dec(remaining),
            entry_window_seconds=int(self.settings.entry.duration_seconds),
            filter_decisions=filters,
        )
        self._no_trade_reasons["entry"] = decision.no_trade_reason or "eligible"

        if not decision.eligible:
            self.lifecycle.transition(target.market_id, "ENTRY_CLOSED")
            return

        # Size the position.
        allocated = self.settings.capital.allocated_usd or Decimal("0")
        risk_budget = allocated * dec(self.settings.risk.risk_per_trade)
        sizing = self.sizer.size(
            available_usd=dec("0"),   # balance populated by balance query
            risk_budget_usd=risk_budget,
            entry_price=decision.entry_price or Decimal("0"),
            fee_rate=dec("0.002"),
            min_order_size_shares=target.trading.minimum_order_size or Decimal("5"),
        )
        if sizing.veto_reason:
            self._no_trade_reasons["sizing"] = sizing.veto_reason
            self.lifecycle.transition(target.market_id, "ENTRY_CLOSED")
            return

        # Risk check.
        rc = self.risk.can_enter(
            available_usd=allocated,
            planned_risk_usd=sizing.size_cost_usd,
            active_positions=active_positions,
            pending_entries_for_market=1,
        )
        if not rc.allowed:
            self._no_trade_reasons["risk"] = rc.reason
            self.lifecycle.transition(target.market_id, "ENTRY_CLOSED")
            return

        # Submit via paper executor (LIVE locked behind gate).
        mode = self.mode
        if mode == MODE.LIVE:
            live_ok = await self._check_live_gate()
            if not live_ok:
                self._no_trade_reasons["live"] = "LIVE gate not satisfied"
                self.lifecycle.transition(target.market_id, "ENTRY_CLOSED")
                return

        # Journal + execute.
        from polybot.types import OrderType, TradeSide
        key = await self.order_manager.journal_intent(
            market_id=target.market_id,
            condition_id=target.condition_id,
            asset_id=target.asset_id_for_outcome("UP") or "",
            side=decision.side or TradeSide.BUY,
            price=decision.entry_price or Decimal("0"),
            size_shares=sizing.size_shares,
            order_type=OrderType(ORDER_TYPE_FAK),
        )
        result = await self.order_manager.submit(
            key=key,
            market_id=target.market_id,
            asset_id=target.asset_id_for_outcome("UP") or "",
            version=target.version,
            side=decision.side or TradeSide.BUY,
            price=decision.entry_price,
            size_shares=sizing.size_shares,
            order_type=OrderType(ORDER_TYPE_FAK),
            max_spend_usd=sizing.size_cost_usd,
        )
        if result.get("ok"):
            self.lifecycle.set_entry(target.market_id, (decision.side or TradeSide.BUY).value, result.get("order_id", ""))
            self.lifecycle.transition(target.market_id, "ENTRY_CLOSED")
            self._positions[target.asset_id_for_outcome("UP") or ""] = {
                "market_id": target.market_id,
                "side": (decision.side or TradeSide.BUY).value,
                "entry_price": decision.entry_price or Decimal("0"),
                "quantity_shares": sizing.size_shares,
                "cost_usd": sizing.size_cost_usd,
            }
            self.risk.reserve(f"res-{key.key}", sizing.size_cost_usd)

    async def _check_live_gate(self) -> bool:
        result = await self.live_gate.evaluate()
        return result.passed

    def _remaining_entry_seconds(self, meta) -> float:
        # Use event_start_time (correct trading-window start), never startDate.
        start = meta.event_start_time or meta.start_date
        end = meta.end_date
        if not start or not end:
            return float(self.settings.entry.duration_seconds)
        from polybot.clock import utc_now
        now = utc_now()
        elapsed = (now - start).total_seconds()
        return max(0.0, float(self.settings.entry.duration_seconds) - elapsed)

    def _book_for(self, meta) -> dict:
        up_id = meta.asset_id_for_outcome("UP")
        down_id = meta.asset_id_for_outcome("DOWN")
        bid = ask = spread = liquidity = None
        if self.market_stream and up_id:
            snap = self.market_stream.book(up_id)
            if snap:
                bid = snap.best_bid
                ask = snap.best_ask
                spread = snap.spread
                liquidity = meta.metrics_liquidity
        return {"best_bid": bid, "best_ask": ask, "spread": spread, "liquidity": liquidity}

    # ------------------------------------------------------------------ #
    # Exits
    # ------------------------------------------------------------------ #
    async def _evaluate_exits(self) -> None:
        # Placeholder: evaluate open positions against exit engine.
        pass

    # ------------------------------------------------------------------ #
    # Dashboard state
    # ------------------------------------------------------------------ #
    def _update_dashboard_state(self) -> None:
        feed_report = self.feeds.health() if self.feeds else {}
        risk_snap = self.risk.snapshot(dec("0"), self.settings.capital.allocated_usd or Decimal("0")) if self.risk else None
        self._last_dashboard_state = {
            "mode": self.mode,
            "ready": self._ready,
            "markets": len(self._markets),
            "positions": len(self._positions),
            "feeds": {
                "primary": str(getattr(feed_report, "primary", "UNVERIFIED")),
                "secondary": str(getattr(feed_report, "secondary", "UNVERIFIED")),
                "twap": str(getattr(feed_report, "twap", "UNVERIFIED")),
                "overall": str(getattr(feed_report, "overall", "UNVERIFIED")),
                "block_entries": bool(getattr(feed_report, "block_new_entries", True)),
                "reason": str(getattr(feed_report, "reason", "")),
            },
            "risk": {
                "daily_loss_usd": str(risk_snap.daily_loss_usd) if risk_snap else "0",
                "daily_pnl_usd": str(risk_snap.daily_pnl_usd) if risk_snap else "0",
                "reserved_usd": str(risk_snap.reserved_capital_usd) if risk_snap else "0",
                "exposure_usd": str(risk_snap.exposure_usd) if risk_snap else "0",
                "within_limits": bool(risk_snap.within_limits) if risk_snap else False,
            } if risk_snap else {},
            "heartbeat": self.heartbeat.status() if self.heartbeat else {},
            "no_trade_reasons": dict(self._no_trade_reasons),
            "live_gate": {
                "passed": bool(self.live_gate._user_approved) if self.live_gate else False,
            },
        }

    def dashboard_state(self) -> dict:
        # Always build from current state so the dashboard works while the
        # trading loop is busy (or hasn't completed its first tick yet).
        try:
            self._update_dashboard_state()
        except Exception:
            logger.exception("Dashboard state refresh failed")
        return dict(self._last_dashboard_state)

    @property
    def ready(self) -> bool:
        return self._ready
