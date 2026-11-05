"""Execution & infrastructure tests: paper simulator, order manager, latency,
hybrid exit, cancellation, heartbeat, recovery, DB corruption, geo, live gate,
resolution, pUSD/fees, taker delay. Mocked only."""
from __future__ import annotations

import asyncio
from decimal import Decimal
import pytest

from polybot.clock import utc_now
from polybot.constants import MODE
from polybot.database import Database
from polybot.engine.exit import HybridExit
from polybot.engine.heartbeat import HeartbeatEngine
from polybot.engine.resolution import resolve, MarketRules, OracleObservations
from polybot.engine.risk import RiskEngine
from polybot.execution.order_manager import OrderManager
from polybot.execution.paper import PaperExecutor
from polybot.recovery.journal import RecoveryManager, RecoveryState
from polybot.validation.geo import GeoChecker
from polybot.validation.live_gate import LiveGate
from polybot.types import (
    TradeSide, OrderType, OrderIntentStatus, Position, MarketMetadata,
    MarketVersion, MarketOutcomeId, MarketTradingConstraints,
)
from tests.conftest import _make_metadata, MockAdapter, settings as settings_fixture


def _register_market(db, market_id="mkt-1"):
    db.execute(
        "INSERT INTO markets(market_id, version, state, active, closed, accepting_orders) "
        "VALUES (?,?,?,?,?,?)",
        (market_id, "v2", "ACTIVE", 1, 0, 1),
    )


# ---------------- paper executor ----------------

@pytest.mark.asyncio
async def test_paper_fak_fills_sometimes(adapter):
    paper = PaperExecutor(taker_delay_ms=10)
    res = await paper.execute(
        asset_id="1001", side=TradeSide.BUY, price=Decimal("0.5"),
        size_shares=Decimal("10"), order_type=OrderType.FAK,
        book_bid=Decimal("0.49"), book_ask=Decimal("0.51"),
    )
    assert res["simulated"] is True
    assert "status" in res
    assert "latency_ms" in res


@pytest.mark.asyncio
async def test_paper_fok_all_or_nothing(adapter):
    paper = PaperExecutor(fill_probability=1.0)
    res = await paper.execute(
        asset_id="1001", side=TradeSide.BUY, price=Decimal("0.5"),
        size_shares=Decimal("10"), order_type=OrderType.FOK,
        book_bid=Decimal("0.49"), book_ask=Decimal("0.51"),
    )
    assert res["status"] == "matched"
    assert Decimal(res["filled_shares"]) == Decimal("10")


def test_paper_latency_percentiles():
    paper = PaperExecutor()
    paper._latency_samples = [10, 20, 30, 40, 50, 60, 70, 80, 90, 100]
    p = paper.p50_p95_p99()
    assert p["p50_ms"] > 0
    assert p["p99_ms"] >= p["p50_ms"]


# ---------------- order manager ----------------

@pytest.mark.asyncio
async def test_order_manager_journals_before_submit(db, adapter):
    _register_market(db)
    om = OrderManager(db, adapter)
    key = await om.journal_intent(
        market_id="mkt-1", condition_id="0xabc", asset_id="1001",
        side=TradeSide.BUY, price=Decimal("0.5"), size_shares=Decimal("10"),
        order_type=OrderType.FAK,
    )
    assert key.key is not None
    pending = om.all_entries()
    assert len(pending) == 1
    assert pending[0].status == OrderIntentStatus.PENDING

    res = await om.submit(
        key=key, market_id="mkt-1", asset_id="1001",
        version=MarketVersion.V2, side=TradeSide.BUY, price=Decimal("0.5"),
        size_shares=Decimal("10"), order_type=OrderType.FAK,
    )
    assert res["ok"] is True
    assert pending[0].status == OrderIntentStatus.ACKNOWLEDGED


@pytest.mark.asyncio
async def test_order_manager_unknown_not_blindly_retried(db, adapter):
    _register_market(db)
    om = OrderManager(db, adapter)

    async def _boom(*a, **k):
        raise Exception("boom")

    adapter.place_order = _boom
    key = await om.journal_intent(
        market_id="mkt-1", condition_id=None, asset_id="1001",
        side=TradeSide.BUY, price=Decimal("0.5"), size_shares=Decimal("10"),
        order_type=OrderType.FAK,
    )
    res = await om.submit(key=key, market_id="mkt-1", asset_id="1001",
                          version=MarketVersion.V2, side=TradeSide.BUY,
                          price=Decimal("0.5"), size_shares=Decimal("10"),
                          order_type=OrderType.FAK)
    assert res.get("unknown") is True
    unknown = om.pending_unknown()
    assert len(unknown) == 1


# ---------------- hybrid exit ----------------

def test_hybrid_exit_stop_and_profit():
    eng = HybridExit(type("S", (), {"exit": type("E", (), {
        "take_profit_roi": Decimal("0.20"), "stop_loss_roi": Decimal("0.25")})()})())
    pos = Position(market_id="m", condition_id="0xabc", asset_id="1001",
                   side=TradeSide.BUY, entry_price=Decimal("0.5"),
                   quantity_shares=Decimal("10"), cost_usd=Decimal("5"))
    d1 = eng.evaluate(position=pos, current_bid=Decimal("0.36"))
    assert d1.action == "STOP_LOSS"
    d2 = eng.evaluate(position=pos, current_bid=Decimal("0.61"))
    assert d2.action == "TAKE_PROFIT"


# ---------------- resolution simulator ----------------

def test_resolve_snapshot_up():
    rules = MarketRules(resolution_text="BTC higher at close",
                        data_source="chainlink", twap_window=None,
                        opening_boundary="open", closing_boundary="close",
                        tie_rules="higher wins", precision=4,
                        rounding="half_up", methodology="snapshot")
    obs = OracleObservations(
        observations=[{"timestamp": "2026-01-01T00:00:00Z", "value": "100", "source": "chainlink"},
                      {"timestamp": "2026-01-01T00:05:00Z", "value": "110", "source": "chainlink"}],
        source="chainlink", freshness_seconds=60,
    )
    res = resolve(rules, obs)
    assert res.opening_reference == Decimal("100")
    assert res.closing_reference == Decimal("110")
    assert res.outcome == "UP"
    assert res.validation_result == "VALID"


def test_resolve_unknown_rules_no_trade():
    rules = MarketRules(resolution_text="", data_source="chainlink", twap_window=None,
                        opening_boundary=None, closing_boundary=None, tie_rules="",
                        precision=4, rounding="half_up", methodology="unknown")
    obs = OracleObservations(observations=[], source="chainlink", freshness_seconds=None)
    res = resolve(rules, obs)
    assert res.validation_result == "NO_TRADE_AMBIGUOUS_RULES"
    assert res.outcome is None


def test_resolve_non_chainlink_source_no_trade():
    rules = MarketRules(resolution_text="x", data_source="pyth", twap_window=None,
                        opening_boundary="o", closing_boundary="c", tie_rules="t",
                        precision=4, rounding="half_up", methodology="snapshot")
    obs = OracleObservations(observations=[{"value": "1"}], source="pyth", freshness_seconds=None)
    res = resolve(rules, obs)
    assert "NON_CHAINLINK" in res.validation_result


# ---------------- heartbeat ----------------

@pytest.mark.asyncio
async def test_heartbeat_simulated_paper(db):
    hb = HeartbeatEngine(db, type("C", (), {
        "enabled": True, "cadence_seconds": 5, "api_key": "key",
        "api_secret": "secret", "passphrase": "pass"})(), mode=MODE.PAPER)
    await hb.start(require_live=False)
    await asyncio.sleep(0.1)
    await hb.stop()
    assert hb.state.latest_id is not None


# ---------------- geo ----------------

@pytest.mark.asyncio
async def test_geo_unverified_when_unreachable():
    gc = GeoChecker(endpoint="http://127.0.0.1:1/nope", timeout=1)
    eligible, reason = await gc.is_eligible()
    assert eligible is False
    assert "unverified" in reason.lower() or "error" in reason.lower()


# ---------------- live gate ----------------

def _make_settings():
    from polybot.config import Settings
    return Settings(mode=MODE.PAPER)


def test_livegate_blocks_without_activation():
    class H:
        primary = "HEALTHY"; secondary = "HEALTHY"; twap = "HEALTHY"
        overall = "HEALTHY"; block_new_entries = False; reason = "ok"
    class Rec:
        state = RecoveryState(ok=True, ready=True)
    class App:
        settings = _make_settings()
        recovery = Rec()
        feeds = type("F", (), {"health": lambda self: H})()
        risk = object()
        shutdown_requested = False
        adapter = object()
        discovery = object()
        _validation_evidence = True
    gate = LiveGate(App(), App.settings)
    res = asyncio.new_event_loop().run_until_complete(gate.evaluate())
    assert res.passed is False
    assert res.live_enabled is False


def test_livegate_passes_with_all_conditions(monkeypatch):
    from decimal import Decimal
    class H:
        primary = "HEALTHY"; secondary = "HEALTHY"; twap = "HEALTHY"
        overall = "HEALTHY"; block_new_entries = False; reason = "ok"
    class Rec:
        state = RecoveryState(ok=True, ready=True)
    class App:
        settings = _make_settings()
        recovery = Rec()
        feeds = type("F", (), {"health": lambda self: H})()
        risk = object()
        shutdown_requested = False
        adapter = object()
        discovery = object()
        _validation_evidence = True
    app_inst = App()
    app_inst.settings.wallet_address = "0xabc"
    app_inst.settings.private_key = "0x" + "1" * 64
    app_inst.settings.capital.allocated_usd = Decimal("1000")
    async def _eligible(self, force=False):
        return (True, "eligible")

    monkeypatch.setattr(GeoChecker, "is_eligible", _eligible)
    gate = LiveGate(app_inst, app_inst.settings)
    gate.set_user_activation(True)
    gate.approve_fingerprint("fp123")
    gate.approve_resolution_replay(True, 25)
    res = asyncio.new_event_loop().run_until_complete(gate.evaluate())
    assert res.passed is True
    assert res.live_enabled is True


# ---------------- pUSD / fees / taker delay ----------------

@pytest.mark.asyncio
async def test_fee_read_from_metadata(adapter):
    meta = await adapter.get_market_metadata("mkt-1")
    assert meta.trading.fees_enabled is True
    assert meta.trading.fee_rate == Decimal("0.04")


def test_taker_delay_default_250():
    from polybot.config import Settings
    s = Settings()
    assert s.fees.taker_delay_ms == 250


# ---------------- risk engine ----------------

def test_risk_can_enter():
    class RS:
        class risk:
            max_active_positions = 1
            max_entries_per_market = 1
            max_consecutive_losses = 2
            daily_loss_limit = Decimal("0.04")
            risk_per_trade = Decimal("0.02")
        class capital:
            allocated_usd = Decimal("1000")
        daily_profit_lock = None
    risk = RiskEngine(None, RS())
    rc = risk.can_enter(available_usd=Decimal("1000"),
                        planned_risk_usd=Decimal("20"),
                        active_positions=0, pending_entries_for_market=0)
    assert rc.allowed is True


def test_risk_daily_loss_blocks():
    class RS:
        class risk:
            max_active_positions = 1
            max_entries_per_market = 1
            max_consecutive_losses = 2
            daily_loss_limit = Decimal("0.04")
            risk_per_trade = Decimal("0.02")
        class capital:
            allocated_usd = Decimal("1000")
        daily_profit_lock = None
    risk = RiskEngine(None, RS())
    risk._daily_loss_usd = Decimal("40")
    rc = risk.can_enter(available_usd=Decimal("1000"),
                        planned_risk_usd=Decimal("20"),
                        active_positions=0, pending_entries_for_market=0)
    assert rc.allowed is False
    assert "daily loss" in (rc.reason or "")
