"""Market-regime detection with hysteresis.

Regimes: TRENDING, SIDEWAYS, HIGH_VOLATILITY, SUDDEN_REVERSAL, UNKNOWN.
HIGH_VOLATILITY, SUDDEN_REVERSAL and UNKNOWN default to NO TRADE until validated.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional

from polybot.decimal_math import clamp, dec, safe_divide
from polybot.types import Regime, RegimeOutput

logger = logging.getLogger("polybot.regime")


class RegimeDetector:
    def __init__(self) -> None:
        self._previous_regime: Optional[Regime] = None
        self._hysteresis_bars: int = 0
        self._hysteresis_required: int = 2

    def classify(
        self,
        momentum: Decimal,
        trend_strength: Decimal,
        volatility: Decimal,
        acceleration: Decimal,
        multi_timeframe_agreement: Decimal,
        reversal_detected: bool,
    ) -> RegimeOutput:
        vol = dec(volatility)
        mom = dec(momentum)
        trend = dec(trend_strength)
        accel = dec(acceleration)
        agreement = clamp(dec(multi_timeframe_agreement), Decimal("0"), Decimal("1"))

        regime = Regime.UNKNOWN

        # High volatility overrides everything -> NO TRADE default.
        if vol > Decimal("0.03") or reversal_detected:
            regime = Regime.HIGH_VOLATILITY if not reversal_detected else Regime.SUDDEN_REVERSAL
        elif agreement < Decimal("0.4"):
            regime = Regime.SIDEWAYS
        elif abs(mom) > Decimal("0.005") and trend > Decimal("0.5"):
            regime = Regime.TRENDING
        else:
            regime = Regime.SIDEWAYS

        # Hysteresis: avoid unstable flips; require N consecutive bars to change.
        if self._previous_regime is not None and regime != self._previous_regime:
            self._hysteresis_bars += 1
            if self._hysteresis_bars < self._hysteresis_required:
                regime = self._previous_regime
            else:
                self._hysteresis_bars = 0
                self._previous_regime = regime
        else:
            self._hysteresis_bars = 0
            if self._previous_regime is None:
                self._previous_regime = regime

        return RegimeOutput(
            regime=regime,
            momentum=mom,
            trend_strength=trend,
            volatility=vol,
            acceleration=accel,
            multi_timeframe_agreement=agreement,
            reversal_detected=reversal_detected,
            hysteresis_applied=self._hysteresis_bars > 0,
            metadata={},
        )

    def no_trade_regimes(self) -> tuple[Regime, ...]:
        return (Regime.HIGH_VOLATILITY, Regime.SUDDEN_REVERSAL, Regime.UNKNOWN)
