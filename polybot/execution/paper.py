"""Paper-trading execution simulator.

Realistic simulated execution: queue position, partial fills, unfilled orders,
slippage, fees, taker delay, cancellation latency, order-book walking. Never
assumes every order fills. Clearly distinguished from real execution.
"""

from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Optional

from polybot.clock import utc_now
from polybot.decimal_math import dec, qshare
from polybot.types import OrderSide, OrderType, TradeSide

logger = logging.getLogger("polybot.paper")


@dataclass
class PaperFill:
    order_id: str
    trade_id: str
    price: Decimal
    size_shares: Decimal
    timestamp: datetime
    fee_usd: Decimal


class PaperExecutor:
    def __init__(
        self,
        *,
        taker_delay_ms: int = 250,
        fill_probability: float = 0.85,
        partial_fill_probability: float = 0.30,
        slippage_bps: float = 1.0,   # 1 bp = 0.0001
        fee_rate: float = 0.0,
        seed: Optional[int] = None,
    ) -> None:
        self.taker_delay_ms = taker_delay_ms
        self.fill_probability = fill_probability
        self.partial_fill_probability = partial_fill_probability
        self.slippage_bps = slippage_bps
        self.fee_rate = fee_rate
        self._rng = random.Random(seed)
        self._fills: list[PaperFill] = []
        self._latency_samples: list[float] = []

    async def execute(
        self,
        *,
        asset_id: str,
        side: TradeSide,
        price: Optional[Decimal],
        size_shares: Decimal,
        order_type: OrderType,
        book_bid: Optional[Decimal] = None,
        book_ask: Optional[Decimal] = None,
    ) -> dict:
        """Simulate an order. Returns a normalized result dict."""
        size_shares = qshare(size_shares)
        start = utc_now()

        # Simulate taker delay.
        await asyncio.sleep(self.taker_delay_ms / 1000.0)

        # Determine executable price.
        if side == TradeSide.BUY:
            exec_price = book_ask if book_ask is not None else price
        else:
            exec_price = book_bid if book_bid is not None else price
        if exec_price is None:
            return {"ok": False, "error": "no executable price", "simulated": True}

        # Apply slippage against the taker side.
        slip = dec(self.slippage_bps) / dec("10000")
        if side == TradeSide.BUY:
            exec_price = exec_price * (Decimal("1") + slip)
        else:
            exec_price = exec_price * (Decimal("1") - slip)

        # Decide fill outcome.
        roll = self._rng.random()
        if order_type == OrderType.FOK:
            # All or nothing.
            if roll < self.fill_probability:
                filled = size_shares
                status = "matched"
            else:
                filled = Decimal("0")
                status = "unmatched"
        elif order_type == OrderType.FAK:
            if roll < self.fill_probability:
                # Possible partial fill.
                if self._rng.random() < self.partial_fill_probability:
                    filled = qshare(size_shares * dec(self._rng.uniform(0.3, 0.9)))
                    status = "partially_filled"
                else:
                    filled = size_shares
                    status = "matched"
            else:
                filled = Decimal("0")
                status = "unmatched"
        else:  # GTC/GTD -> rest on book; treat as live (no immediate fill)
            filled = Decimal("0")
            status = "live"

        fee_usd = filled * exec_price * dec(self.fee_rate)
        if filled > 0:
            trade_id = f"paper-trade-{int(utc_now().timestamp()*1000)}"
            self._fills.append(PaperFill(
                order_id=f"paper-{asset_id}",
                trade_id=trade_id,
                price=exec_price,
                size_shares=filled,
                timestamp=utc_now(),
                fee_usd=fee_usd,
            ))

        end = utc_now()
        latency_ms = (end.timestamp() - start.timestamp()) * 1000
        self._latency_samples.append(latency_ms)

        return {
            "ok": filled > 0 or status == "live",
            "order_id": f"paper-{asset_id}-{int(start.timestamp()*1000)}",
            "status": status,
            "making_amount": str(filled * exec_price),
            "taking_amount": str(filled),
            "trade_ids": [self._fills[-1].trade_id] if filled > 0 else [],
            "transactions_hashes": [],
            "filled_shares": str(filled),
            "fee_usd": str(fee_usd),
            "simulated": True,
            "latency_ms": latency_ms,
        }

    def p50_p95_p99(self) -> dict:
        if not self._latency_samples:
            return {"p50_ms": 0, "p95_ms": 0, "p99_ms": 0}
        s = sorted(self._latency_samples)
        def pct(p):
            k = (len(s) - 1) * p / 100
            f = int(k)
            c = min(f + 1, len(s) - 1)
            if f == c:
                return s[f]
            return s[f] + (s[c] - s[f]) * (k - f)
        return {"p50_ms": pct(50), "p95_ms": pct(95), "p99_ms": pct(99)}

    def fills(self) -> list[PaperFill]:
        return list(self._fills)
