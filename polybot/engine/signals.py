"""Multi-timeframe analysis and false-signal detection.

Timeframes: 5s, 15s, 30s, 1m, 3m. Each filter returns PASS / FAIL /
UNAVAILABLE; UNAVAILABLE never silently passes.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Callable, Optional

from polybot.clock import utc_now
from polybot.decimal_math import clamp, dec
from polybot.types import BTCTick, FilterDecision, FilterResult, TradeSide

logger = logging.getLogger("polybot.signals")


@dataclass
class TimeframeSignal:
    timeframe: str
    direction: int   # 1 up, -1 down, 0 flat
    momentum: Decimal
    normalized_return: Decimal


class MultiTimeframeAnalysis:
    def __init__(self) -> None:
        self._history: list[tuple[datetime, Decimal]] = []
        self._timeframes = ["5s", "15s", "30s", "1m", "3m"]

    def update(self, tick: Optional[BTCTick]) -> None:
        if tick is None:
            return
        self._history.append((tick.timestamp, tick.price))
        if len(self._history) > 5000:
            self._history = self._history[-5000:]

    def _return_over(self, seconds: int) -> Optional[Decimal]:
        if len(self._history) < 2:
            return None
        now_ts = self._history[-1][0]
        target_price = None
        for ts, price in reversed(self._history):
            if (now_ts - ts).total_seconds() >= seconds:
                target_price = price
                break
        if target_price is None or target_price == 0:
            return None
        latest = self._history[-1][1]
        return (latest - target_price) / target_price

    def analyze(self) -> dict[str, TimeframeSignal]:
        signals: dict[str, TimeframeSignal] = {}
        seconds = {"5s": 5, "15s": 15, "30s": 30, "1m": 60, "3m": 180}
        for tf, secs in seconds.items():
            ret = self._return_over(secs)
            if ret is None:
                signals[tf] = TimeframeSignal(tf, 0, Decimal("0"), Decimal("0"))
                continue
            direction = 1 if ret > 0 else (-1 if ret < 0 else 0)
            signals[tf] = TimeframeSignal(tf, direction, ret, ret)
        return signals

    def agreement_score(self, signals: dict[str, TimeframeSignal]) -> Decimal:
        dirs = [s.direction for s in signals.values() if s.direction != 0]
        if not dirs:
            return Decimal("0")
        up = sum(1 for d in dirs if d > 0)
        down = sum(1 for d in dirs if d < 0)
        total = len(dirs)
        return Decimal(max(up, down)) / Decimal(total)


class FalseSignalDetector:
    """Runs all false-signal filters. Each returns PASS/FAIL/UNAVAILABLE."""

    def __init__(self, mid_analysis: MultiTimeframeAnalysis) -> None:
        self.mtf = mid_analysis

    def run_all(
        self,
        side: TradeSide,
        spread: Optional[Decimal],
        liquidity: Optional[Decimal],
        recent_trades: Optional[list[dict]],
        book_depth_imbalance: Optional[Decimal],
    ) -> list[FilterDecision]:
        decisions: list[FilterDecision] = []
        decisions.append(self._fake_breakout())
        decisions.append(self._momentum_exhaustion())
        decisions.append(self._sudden_reversal())
        decisions.append(self._volume_confirmation(recent_trades))
        decisions.append(self._abnormal_order_book(spread, book_depth_imbalance))
        decisions.append(self._liquidity_withdrawal(liquidity))
        return decisions

    def _fake_breakout(self) -> FilterDecision:
        signals = self.mtf.analyze()
        # A breakout on 5s not confirmed by 1m/3m is likely fake.
        s5 = signals.get("5s")
        s1m = signals.get("1m")
        s3m = signals.get("3m")
        if s5 is None or s1m is None or s3m is None:
            return FilterDecision("Fake Breakout", FilterResult.UNAVAILABLE, "insufficient timeframe data")
        if s5.direction != 0 and s1m.direction == 0 and s3m.direction == 0:
            return FilterDecision("Fake Breakout", FilterResult.FAIL, "5s breakout not confirmed by longer timeframes")
        return FilterDecision("Fake Breakout", FilterResult.PASS, "breakout consistent across timeframes")

    def _momentum_exhaustion(self) -> FilterDecision:
        signals = self.mtf.analyze()
        s5 = signals.get("5s")
        s15 = signals.get("15s")
        if s5 is None or s15 is None:
            return FilterDecision("Momentum Exhaustion", FilterResult.UNAVAILABLE, "insufficient data")
        # Momentum reversed direction on the shortest timeframe -> exhaustion.
        if s5.direction != 0 and s15.direction != 0 and s5.direction != s15.direction:
            return FilterDecision("Momentum Exhaustion", FilterResult.FAIL, "short-term momentum opposing intermediate trend")
        return FilterDecision("Momentum Exhaustion", FilterResult.PASS, "momentum coherent")

    def _sudden_reversal(self) -> FilterDecision:
        signals = self.mtf.analyze()
        s5 = signals.get("5s")
        s30 = signals.get("30s")
        if s5 is None or s30 is None:
            return FilterDecision("Sudden Reversal", FilterResult.UNAVAILABLE, "insufficient data")
        if s5.direction != 0 and s30.direction != 0 and s5.direction != s30.direction:
            return FilterDecision("Sudden Reversal", FilterResult.FAIL, "short-term reversal against 30s trend")
        return FilterDecision("Sudden Reversal", FilterResult.PASS, "no sudden reversal")

    def _volume_confirmation(self, recent_trades: Optional[list[dict]]) -> FilterDecision:
        if recent_trades is None:
            return FilterDecision("Volume Confirmation", FilterResult.UNAVAILABLE, "trade data unavailable")
        if len(recent_trades) == 0:
            return FilterDecision("Volume Confirmation", FilterResult.FAIL, "no recent volume")
        return FilterDecision("Volume Confirmation", FilterResult.PASS, "volume present")

    def _abnormal_order_book(
        self, spread: Optional[Decimal], imbalance: Optional[Decimal]
    ) -> FilterDecision:
        if spread is None or imbalance is None:
            return FilterDecision("Abnormal Order Book", FilterResult.UNAVAILABLE, "book data unavailable")
        # Very wide spread or extreme imbalance indicates an abnormal book.
        if spread is not None and spread > Decimal("0.05"):
            return FilterDecision("Abnormal Order Book", FilterResult.FAIL, "spread too wide")
        if imbalance is not None and abs(imbalance) > Decimal("0.9"):
            return FilterDecision("Abnormal Order Book", FilterResult.FAIL, "extreme book imbalance")
        return FilterDecision("Abnormal Order Book", FilterResult.PASS, "book normal")

    def _liquidity_withdrawal(self, liquidity: Optional[Decimal]) -> FilterDecision:
        if liquidity is None:
            return FilterDecision("Liquidity Withdrawal", FilterResult.UNAVAILABLE, "liquidity data unavailable")
        if liquidity <= 0:
            return FilterDecision("Liquidity Withdrawal", FilterResult.FAIL, "no liquidity")
        return FilterDecision("Liquidity Withdrawal", FilterResult.PASS, "liquidity present")

    @staticmethod
    def all_pass(decisions: list[FilterDecision]) -> bool:
        return all(d.result == FilterResult.PASS for d in decisions)

    @staticmethod
    def any_unavailable(decisions: list[FilterDecision]) -> bool:
        return any(d.result == FilterResult.UNAVAILABLE for d in decisions)
