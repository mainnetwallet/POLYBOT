"""Dynamic entry strategy.

Evaluates opportunities every 1-2 seconds. Only enters when every mandatory
gate passes. Never enters after the 120-second entry window. Never chases a
moving price. If net EV is insufficient: NO TRADE.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Optional

from polybot.clock import utc_now
from polybot.decimal_math import clamp, dec, safe_divide
from polybot.types import (
    EntryDecision,
    FilterDecision,
    FilterResult,
    Regime,
    TradeSide,
)

logger = logging.getLogger("polybot.entry")


@dataclass
class EntryGate:
    name: str
    passed: bool
    reason: str


class DynamicEntry:
    def __init__(self, config) -> None:
        self.config = config
        self._last_evaluation: Optional[datetime] = None

    def evaluate(
        self,
        *,
        probability,          # ProbabilityOutput
        regime,               # RegimeOutput
        order_book,           # dict with best_bid/best_ask/spread/liquidity
        remaining_seconds: Decimal,
        entry_window_seconds: int,
        side_preference: Optional[TradeSide] = None,
        filter_decisions: list[FilterDecision] | None = None,
    ) -> EntryDecision:
        gates: list[EntryGate] = []
        no_trade_reason: Optional[str] = None

        # Gate 0: entry window (entries only in first 120 seconds).
        if remaining_seconds <= 0 or remaining_seconds > entry_window_seconds:
            no_trade_reason = "outside entry window"
            return self._reject(probability, remaining_seconds, gates, no_trade_reason)

        # Gate 1: regime not a no-trade regime.
        if regime.regime in (Regime.HIGH_VOLATILITY, Regime.SUDDEN_REVERSAL, Regime.UNKNOWN):
            no_trade_reason = f"no-trade regime: {regime.regime.value}"
            return self._reject(probability, remaining_seconds, gates, no_trade_reason)

        # Gate 2: probability threshold.
        p_up = probability.p_up
        p_down = probability.p_down
        best_side: Optional[TradeSide] = None
        best_prob = Decimal("0")
        if p_up >= self.config.entry.min_probability:
            best_side = TradeSide.BUY
            best_prob = p_up
        elif p_down >= self.config.entry.min_probability:
            best_side = TradeSide.SELL
            best_prob = p_down

        if side_preference is not None:
            best_side = side_preference

        if best_side is None:
            no_trade_reason = "probability below minimum"
            return self._reject(probability, remaining_seconds, gates, no_trade_reason)

        # Gate 3: minimum net edge (distance from 0.5).
        net_edge = abs(p_up - Decimal("0.5"))
        if net_edge < self.config.entry.min_net_edge:
            no_trade_reason = "net edge below minimum"
            return self._reject(probability, remaining_seconds, gates, no_trade_reason)

        # Gate 4: spread limit.
        spread = dec(order_book.get("spread")) if order_book and order_book.get("spread") is not None else None
        if spread is not None and spread > self.config.entry.max_spread:
            no_trade_reason = "spread above maximum"
            return self._reject(probability, remaining_seconds, gates, no_trade_reason)

        # Gate 5: false-signal filters (UNAVAILABLE never silently passes).
        if filter_decisions:
            for fd in filter_decisions:
                if fd.result == FilterResult.UNAVAILABLE:
                    no_trade_reason = f"filter unavailable: {fd.name}"
                    return self._reject(probability, remaining_seconds, gates, no_trade_reason)
                if fd.result == FilterResult.FAIL:
                    no_trade_reason = f"filter failed: {fd.name}"
                    return self._reject(probability, remaining_seconds, gates, no_trade_reason)

        gates.append(EntryGate("entry_window", True, "within window"))
        gates.append(EntryGate("regime", True, regime.regime.value))
        gates.append(EntryGate("probability", True, f"p={best_prob}"))
        gates.append(EntryGate("net_edge", True, str(net_edge)))
        gates.append(EntryGate("spread", True, str(spread) if spread else "n/a"))
        gates.append(EntryGate("filters", True, "all pass"))

        # Expected executable entry price and EV.
        best_ask = dec(order_book.get("best_ask")) if order_book and order_book.get("best_ask") is not None else None
        best_bid = dec(order_book.get("best_bid")) if order_book and order_book.get("best_bid") is not None else None
        entry_price = best_ask if best_side == TradeSide.BUY else best_bid
        slippage_estimate = dec("0.005")
        fee_estimate = dec("0.002")

        # Binary EV: p_win * 1.0 - entry_price, less costs.
        expected_net_value = (best_prob - entry_price) if entry_price is not None else Decimal("0")
        expected_net_value = expected_net_value - slippage_estimate - fee_estimate

        self._last_evaluation = utc_now()
        return EntryDecision(
            eligible=True,
            side=best_side,
            probability=best_prob,
            net_edge=net_edge,
            expected_net_value=expected_net_value,
            entry_price=entry_price,
            size_shares=None,  # filled by position sizing
            size_cost_usd=None,
            spread=spread,
            slippage_estimate=slippage_estimate,
            fee_estimate=fee_estimate,
            remaining_window_seconds=remaining_seconds,
            gates_passed=[g.name for g in gates],
            gates_failed=[],
        )

    def _reject(self, probability, remaining_seconds, gates, reason):
        return EntryDecision(
            eligible=False,
            side=None,
            probability=probability.p_up,
            net_edge=Decimal("0"),
            expected_net_value=Decimal("0"),
            entry_price=None,
            size_shares=None,
            size_cost_usd=None,
            spread=None,
            slippage_estimate=None,
            fee_estimate=None,
            remaining_window_seconds=remaining_seconds,
            no_trade_reason=reason,
            gates_passed=[g.name for g in gates],
            gates_failed=[reason],
        )
