"""Trading intelligence tests: prediction, regime, signals, entry, profit,
sizing, risk, exit. Mocked only."""
from __future__ import annotations

from decimal import Decimal
import pytest

from polybot.clock import utc_now
from polybot.engine.prediction import PredictionModel, ModelInputs
from polybot.engine.regime import RegimeDetector
from polybot.engine.signals import MultiTimeframeAnalysis, FalseSignalDetector
from polybot.engine.entry import DynamicEntry
from polybot.engine.profit import ProfitOptimizer
from polybot.engine.sizing import PositionSizer
from polybot.engine.risk import RiskEngine
from polybot.engine.exit import HybridExit
from polybot.types import (
    Regime, FilterResult, TradeSide, FilterDecision, BTCTick,
)
from tests.conftest import _make_metadata


# ---------------- prediction ----------------

def test_prediction_up_edge():
    model = PredictionModel()
    regime_out = type("RO", (), {"regime": Regime.TRENDING,
        "momentum": Decimal("0.01"), "trend_strength": Decimal("0.8"),
        "volatility": Decimal("0.005"), "acceleration": Decimal("0"),
        "multi_timeframe_agreement": Decimal("0.9"),
        "reversal_detected": False, "hysteresis_applied": False,
        "metadata": {}})()
    inputs = ModelInputs(
        btc_price=Decimal("100000"),
        btc_return_5s=Decimal("0.001"),
        btc_return_15s=Decimal("0.0015"),
        btc_return_30s=Decimal("0.002"),
        btc_return_1m=Decimal("0.003"),
        btc_return_3m=Decimal("0.004"),
        volatility=Decimal("0.005"),
        twap=Decimal("99900"),
        twap_window_seconds=60,
        opening_reference=Decimal("99800"),
        remaining_window_seconds=Decimal("60"),
        market_price_up=Decimal("0.5"),
        market_price_down=Decimal("0.5"),
        spread=Decimal("0.01"),
        time_remaining_seconds=Decimal("60"),
    )
    out = model.predict(inputs, regime_out)
    assert 0 < out.p_up < 1
    assert abs(out.p_up + out.p_down - 1) < Decimal("0.0001")
    assert out.expected_settlement_outcome in ("UP", "DOWN", None)


# ---------------- regime ----------------

def test_regime_high_vol_no_trade():
    det = RegimeDetector()
    out = det.classify(
        momentum=Decimal("0"), trend_strength=Decimal("0.5"),
        volatility=Decimal("0.05"), acceleration=Decimal("0"),
        multi_timeframe_agreement=Decimal("0.9"), reversal_detected=False,
    )
    assert out.regime in RegimeDetector().no_trade_regimes()


def test_regime_trending():
    det = RegimeDetector()
    out = det.classify(
        momentum=Decimal("0.01"), trend_strength=Decimal("0.8"),
        volatility=Decimal("0.005"), acceleration=Decimal("0"),
        multi_timeframe_agreement=Decimal("0.9"), reversal_detected=False,
    )
    assert out.regime == Regime.TRENDING


# ---------------- multi-timeframe / false signals ----------------

def test_mtf_agreement():
    mtf = MultiTimeframeAnalysis()
    tick = BTCTick(timestamp=utc_now(), price=Decimal("100"), source="x",
                   provider="chainlink")
    mtf.update(tick)
    sigs = mtf.analyze()
    assert set(sigs.keys()) == {"5s", "15s", "30s", "1m", "3m"}


def test_false_signal_results_are_pass_or_fail():
    mtf = MultiTimeframeAnalysis()
    det = FalseSignalDetector(mtf)
    decisions = det.run_all(
        TradeSide.BUY, spread=Decimal("0.01"), liquidity=Decimal("1000"),
        recent_trades=[{"p": "1"}], book_depth_imbalance=Decimal("0.1"),
    )
    assert all(d.result in (FilterResult.PASS, FilterResult.FAIL)
               for d in decisions)
    assert any(d.result == FilterResult.PASS for d in decisions)


# ---------------- entry ----------------

class FakeEntrySettings:
    class entry:
        min_probability = Decimal("0.60")
        min_net_edge = Decimal("0.05")
        max_spread = Decimal("0.03")
        max_slippage = Decimal("0.01")
        duration_seconds = 120


def test_entry_eligible():
    entry = DynamicEntry(FakeEntrySettings())
    class Prob:
        p_up = Decimal("0.70"); p_down = Decimal("0.30")
    class Reg:
        regime = Regime.TRENDING
    book = {"best_bid": Decimal("0.48"), "best_ask": Decimal("0.50"),
            "spread": Decimal("0.02"), "liquidity": Decimal("1000")}
    dec = entry.evaluate(
        probability=Prob(), regime=Reg(), order_book=book,
        remaining_seconds=Decimal("60"), entry_window_seconds=120,
        filter_decisions=[],
    )
    assert dec.eligible is True
    assert dec.side == TradeSide.BUY


def test_entry_blocked_after_window():
    entry = DynamicEntry(FakeEntrySettings())
    class Prob:
        p_up = Decimal("0.70"); p_down = Decimal("0.30")
    class Reg:
        regime = Regime.TRENDING
    dec = entry.evaluate(
        probability=Prob(), regime=Reg(), order_book={"spread": Decimal("0.01")},
        remaining_seconds=Decimal("-5"), entry_window_seconds=120,
        filter_decisions=[],
    )
    assert dec.eligible is False
    assert "window" in (dec.no_trade_reason or "")


def test_entry_blocked_high_vol():
    entry = DynamicEntry(FakeEntrySettings())
    class Prob:
        p_up = Decimal("0.70"); p_down = Decimal("0.30")
    class Reg:
        regime = Regime.HIGH_VOLATILITY
    dec = entry.evaluate(
        probability=Prob(), regime=Reg(), order_book={"spread": Decimal("0.01")},
        remaining_seconds=Decimal("60"), entry_window_seconds=120,
        filter_decisions=[],
    )
    assert dec.eligible is False


# ---------------- profit / EV ----------------

def test_ev_positive():
    opt = ProfitOptimizer(FakeEntrySettings())
    res = opt.expected_value(
        p_win=Decimal("0.7"), entry_price=Decimal("0.5"), exit_price=Decimal("1.0"),
        size_shares=Decimal("10"), fee_rate=Decimal("0.002"),
        slippage=Decimal("0.001"), spread=Decimal("0.01"),
    )
    assert res["positive_expectancy"] is True
    assert res["net_ev_usd"] > 0


# ---------------- sizing ----------------

def test_sizing_respects_risk():
    sizer = PositionSizer(FakeEntrySettings())
    res = sizer.size(
        available_usd=Decimal("1000"), risk_budget_usd=Decimal("20"),
        entry_price=Decimal("0.5"), fee_rate=Decimal("0.002"),
        min_order_size_shares=Decimal("5"),
    )
    assert res.size_shares > 0
    assert res.within_risk_budget is True
    assert res.size_cost_usd <= Decimal("20") + Decimal("0.01")


def test_sizing_below_min_order_no_trade():
    sizer = PositionSizer(FakeEntrySettings())
    res = sizer.size(
        available_usd=Decimal("1"), risk_budget_usd=Decimal("0.01"),
        entry_price=Decimal("0.5"), fee_rate=Decimal("0.002"),
        min_order_size_shares=Decimal("1000"),
    )
    assert res.meets_minimum_order is False
    assert res.veto_reason is not None


# ---------------- risk ----------------

class FakeRiskSettings:
    class risk:
        max_active_positions = 1
        max_entries_per_market = 1
        max_consecutive_losses = 2
        daily_loss_limit = Decimal("0.04")
        risk_per_trade = Decimal("0.02")
    class capital:
        allocated_usd = Decimal("1000")
    daily_profit_lock = None


def test_risk_can_enter():
    risk = RiskEngine(None, FakeRiskSettings())
    rc = risk.can_enter(available_usd=Decimal("1000"),
                        planned_risk_usd=Decimal("20"),
                        active_positions=0, pending_entries_for_market=0)
    assert rc.allowed is True


def test_risk_daily_loss_blocks():
    risk = RiskEngine(None, FakeRiskSettings())
    risk._daily_loss_usd = Decimal("40")
    rc = risk.can_enter(available_usd=Decimal("1000"),
                        planned_risk_usd=Decimal("20"),
                        active_positions=0, pending_entries_for_market=0)
    assert rc.allowed is False
    assert "daily loss" in (rc.reason or "")


# ---------------- exit ----------------

def test_exit_stop_loss():
    class ES:
        class exit:
            take_profit_roi = Decimal("0.20")
            stop_loss_roi = Decimal("0.25")
    eng = HybridExit(ES())
    pos = type("P", (), {"side": TradeSide.BUY, "entry_price": Decimal("0.5"),
        "quantity_shares": Decimal("10"), "trailing_stop_price": None,
        "metadata": {}})()
    dec = eng.evaluate(position=pos, current_bid=Decimal("0.36"))
    assert dec.action == "STOP_LOSS"


def test_exit_take_profit():
    class ES:
        class exit:
            take_profit_roi = Decimal("0.20")
            stop_loss_roi = Decimal("0.25")
    eng = HybridExit(ES())
    pos = type("P", (), {"side": TradeSide.BUY, "entry_price": Decimal("0.5"),
        "quantity_shares": Decimal("10"), "trailing_stop_price": None,
        "metadata": {}})()
    dec = eng.evaluate(position=pos, current_bid=Decimal("0.61"))
    assert dec.action == "TAKE_PROFIT"
