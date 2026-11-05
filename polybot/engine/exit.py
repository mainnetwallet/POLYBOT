"""Hybrid exit engine.

Priority:
Emergency protection -> Stop Loss -> Trailing Stop -> Take Profit ->
Probability Reversal -> Hold-vs-Sell EV comparison -> Resolution.

Uses actual executable bid prices and costs. Never assumes exit completion
before confirmed fills. Experimental exit models run in SHADOW until validated.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from polybot.decimal_math import clamp, dec, safe_divide
from polybot.types import ExitDecision, ExitPriority, Position

logger = logging.getLogger("polybot.exit")


class HybridExit:
    def __init__(self, config, feature_levels: dict | None = None) -> None:
        self.config = config
        self.feature_levels = feature_levels or {}

    def evaluate(
        self,
        *,
        position: Position,
        current_bid: Decimal,
        current_prob_up: Optional[Decimal] = None,
        entry_probability: Optional[Decimal] = None,
        liquidity_deteriorating: bool = False,
        time_to_resolution_seconds: Optional[Decimal] = None,
    ) -> ExitDecision:
        side = position.side
        entry_price = position.entry_price
        qty = position.quantity_shares

        # Unrealized ROI for a long (BUY) position: (bid - entry)/entry.
        # For SELL positions, invert. We model BUY of UP share; price 1 = win.
        if side.value == "BUY":
            roi = (current_bid - entry_price) / entry_price if entry_price > 0 else Decimal("0")
        else:
            roi = (entry_price - current_bid) / entry_price if entry_price > 0 else Decimal("0")

        take_profit = dec(self.config.exit.take_profit_roi)
        stop_loss = dec(self.config.exit.stop_loss_roi)

        # 1. Emergency protection: none beyond stop loss here.
        # 2. Stop Loss.
        if roi <= -stop_loss:
            return ExitDecision(
                action="STOP_LOSS",
                priority=ExitPriority.STOP_LOSS,
                price=current_bid,
                size_shares=qty,
                reason=f"stop loss triggered ROI={roi:.4%}",
                executable=True,
            )

        # 3. Trailing Stop (SHADOW unless activated).
        if self.feature_levels.get("trailing_stop") == "ACTIVE" and position.trailing_stop_price:
            if current_bid <= position.trailing_stop_price:
                return ExitDecision(
                    action="TRAILING_STOP",
                    priority=ExitPriority.TRAILING_STOP,
                    price=current_bid,
                    size_shares=qty,
                    reason="trailing stop hit",
                    executable=True,
                )

        # 4. Take Profit.
        if roi >= take_profit:
            return ExitDecision(
                action="TAKE_PROFIT",
                priority=ExitPriority.TAKE_PROFIT,
                price=current_bid,
                size_shares=qty,
                reason=f"take profit triggered ROI={roi:.4%}",
                executable=True,
            )

        # 5. Probability Reversal Exit (SHADOW unless activated).
        if (
            self.feature_levels.get("probability_reversal") == "ACTIVE"
            and current_prob_up is not None
            and entry_probability is not None
        ):
            if side.value == "BUY" and current_prob_up < entry_probability * dec("0.7"):
                return ExitDecision(
                    action="PROBABILITY_REVERSAL",
                    priority=ExitPriority.PROBABILITY_REVERSAL,
                    price=current_bid,
                    size_shares=qty,
                    reason="probability reversal exit",
                    executable=True,
                )

        # 6. Liquidity Deterioration Exit.
        if liquidity_deteriorating:
            return ExitDecision(
                action="LIQUIDITY_DETERIORATION",
                priority=ExitPriority.EV_COMPARISON,
                price=current_bid,
                size_shares=qty,
                reason="bid-side liquidity deteriorated",
                executable=True,
            )

        # 7. Time-Based Exit: near resolution, hold-to-resolution unless EV of
        #    selling now exceeds holding. Default: hold to resolution.
        if time_to_resolution_seconds is not None and time_to_resolution_seconds <= 60:
            return ExitDecision(
                action="HOLD_TO_RESOLUTION",
                priority=ExitPriority.RESOLUTION,
                price=None,
                size_shares=None,
                reason="within final minute of resolution; holding",
                executable=False,
            )

        return ExitDecision(
            action="NONE",
            priority=ExitPriority.RESOLUTION,
            price=None,
            size_shares=None,
            reason="no exit condition met",
            executable=False,
        )
