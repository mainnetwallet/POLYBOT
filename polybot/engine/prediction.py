"""Prediction engine: TWAP-aware statistical model.

Calculates UP/DOWN probability, confidence, uncertainty interval and expected
settlement outcome using only data available at the decision timestamp. Does
NOT assume an instantaneous-price formula is correct for TWAP settlement.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Optional

from polybot.clock import utc_now
from polybot.decimal_math import (
    clamp,
    dec,
    pct,
    round_half_up,
    safe_divide,
)
from polybot.types import (
    BTCTick,
    ProbabilityOutput,
    Regime,
    RegimeOutput,
    TWAPTick,
)

logger = logging.getLogger("polybot.prediction")

# Initial research thresholds (unvalidated, configurable).
DEFAULT_MIN_PROBABILITY = Decimal("0.60")
DEFAULT_MIN_NET_EDGE = Decimal("0.05")  # 5 percentage points


@dataclass
class ModelInputs:
    btc_price: Optional[Decimal]
    btc_return_5s: Optional[Decimal]
    btc_return_15s: Optional[Decimal]
    btc_return_30s: Optional[Decimal]
    btc_return_1m: Optional[Decimal]
    btc_return_3m: Optional[Decimal]
    volatility: Optional[Decimal]
    twap: Optional[Decimal]
    twap_window_seconds: Optional[int]
    opening_reference: Optional[Decimal]
    remaining_window_seconds: Optional[Decimal]
    market_price_up: Optional[Decimal]   # Polymarket UP share price
    market_price_down: Optional[Decimal]
    spread: Optional[Decimal]
    time_remaining_seconds: Optional[Decimal]


class PredictionModel:
    """Transparent statistical model (no ML). ML is opt-in only after
    validated walk-forward training (see strategy/intelligence)."""

    def __init__(self) -> None:
        self._price_history: list[tuple[datetime, Decimal]] = []

    def update_price_history(self, tick: Optional[BTCTick]) -> None:
        if tick is None:
            return
        self._price_history.append((tick.timestamp, tick.price))
        # Keep a bounded window.
        if len(self._price_history) > 2000:
            self._price_history = self._price_history[-2000:]

    def _recent_return(self, seconds: int) -> Optional[Decimal]:
        if len(self._price_history) < 2:
            return None
        now = self._price_history[-1][0]
        target = None
        for ts, price in reversed(self._price_history):
            if (now - ts).total_seconds() >= seconds:
                target = price
                break
        if target is None:
            return None
        latest = self._price_history[-1][1]
        if target == 0:
            return None
        return (latest - target) / target

    def predict(self, inputs: ModelInputs, regime: RegimeOutput) -> ProbabilityOutput:
        """Compute UP/DOWN probability from TWAP-relative distance and momentum."""
        up_prob = Decimal("0.5")
        down_prob = Decimal("0.5")
        confidence = Decimal("0.5")

        # 1. TWAP-relative drift: how far the current price is from the opening
        #    reference relative to the TWAP, and direction of momentum.
        drift = Decimal("0")
        if inputs.opening_reference is not None and inputs.btc_price is not None and inputs.opening_reference != 0:
            drift = (inputs.btc_price - inputs.opening_reference) / inputs.opening_reference

        momentum = Decimal("0")
        if inputs.btc_return_30s is not None:
            momentum += inputs.btc_return_30s
        if inputs.btc_return_1m is not None:
            momentum += inputs.btc_return_1m
        momentum = clamp(momentum, Decimal("-0.10"), Decimal("0.10"))

        # Combine: price above opening reference with positive momentum -> UP.
        raw_up = Decimal("0.5") + (drift * Decimal("10")) + (momentum * Decimal("5"))

        # 2. TWAP proximity: if the market price is far from the TWAP, mean
        #    reversion reduces confidence in a directional breakout.
        twap_pull = Decimal("0")
        if (
            inputs.twap is not None
            and inputs.btc_price is not None
            and inputs.twap != 0
        ):
            twap_pull = (inputs.btc_price - inputs.twap) / inputs.twap
            raw_up -= twap_pull * Decimal("3")

        # 3. Regime adjustment.
        if regime.regime == Regime.HIGH_VOLATILITY or regime.regime == Regime.SUDDEN_REVERSAL or regime.regime == Regime.UNKNOWN:
            raw_up = Decimal("0.5")  # no edge in unstable regimes
        elif regime.regime == Regime.TRENDING and regime.momentum > 0:
            raw_up += Decimal("0.05")
        elif regime.regime == Regime.TRENDING and regime.momentum < 0:
            raw_up -= Decimal("0.05")
        elif regime.regime == Regime.SIDEWAYS:
            raw_up = Decimal("0.5") + (raw_up - Decimal("0.5")) * Decimal("0.5")

        # Clamp to probability.
        up_prob = clamp(raw_up, Decimal("0.05"), Decimal("0.95"))
        down_prob = Decimal("1") - up_prob

        # Confidence: higher when momentum and TWAP agree and volatility low.
        agreement = Decimal("1")
        if inputs.btc_return_1m is not None and drift != 0:
            if (inputs.btc_return_1m > 0 and drift > 0) or (inputs.btc_return_1m < 0 and drift < 0):
                agreement = Decimal("1.2")
            else:
                agreement = Decimal("0.8")
        conf = Decimal("0.5") * agreement
        if regime.regime in (Regime.HIGH_VOLATILITY, Regime.SUDDEN_REVERSAL, Regime.UNKNOWN):
            conf = Decimal("0.3")
        confidence = clamp(conf, Decimal("0.0"), Decimal("0.95"))

        # Uncertainty interval around the probability.
        half_width = clamp((Decimal("1") - confidence) * Decimal("0.25"), Decimal("0"), Decimal("0.25"))
        interval = (
            clamp(up_prob - half_width, Decimal("0"), Decimal("1")),
            clamp(up_prob + half_width, Decimal("0"), Decimal("1")),
        )

        # Expected settlement outcome: which side is more likely AND far enough
        # from the current market-implied probability to have an edge.
        implied_up = Decimal("0.5")
        if inputs.market_price_up is not None and inputs.market_price_up != 0:
            implied_up = inputs.market_price_up  # share price ≈ implied probability

        edge = up_prob - implied_up
        net_edge = abs(edge)
        expected_outcome: Optional[str] = None
        if up_prob >= DEFAULT_MIN_PROBABILITY and net_edge >= DEFAULT_MIN_NET_EDGE and edge > 0:
            expected_outcome = "UP"
        elif down_prob >= DEFAULT_MIN_PROBABILITY and net_edge >= DEFAULT_MIN_NET_EDGE and edge < 0:
            expected_outcome = "DOWN"

        signal_quality = clamp(confidence * clamp(net_edge, Decimal("0"), Decimal("1")), Decimal("0"), Decimal("1"))

        return ProbabilityOutput(
            p_up=round_half_up(up_prob, 4),
            p_down=round_half_up(down_prob, 4),
            confidence=round_half_up(confidence, 4),
            uncertainty_interval=interval,
            expected_settlement_outcome=expected_outcome,
            regime=regime.regime,
            signal_quality=round_half_up(signal_quality, 4),
            metadata={
                "drift": str(drift),
                "momentum": str(momentum),
                "twap_pull": str(twap_pull),
                "implied_up": str(implied_up),
                "edge": str(edge),
            },
        )
