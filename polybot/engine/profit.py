"""Profit Optimization Engine.

Calculates net expected return after all applicable fees, spreads and
execution costs. Optimizes long-term expectancy and drawdown, not win rate.
Never uses future information.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Optional

from polybot.decimal_math import clamp, dec, safe_divide
from polybot.types import EntryDecision

logger = logging.getLogger("polybot.profit")


class ProfitOptimizer:
    """Evaluates entry-price EV and ranks/rejects opportunities."""

    def __init__(self, config) -> None:
        self.config = config

    def expected_value(
        self,
        *,
        p_win: Decimal,
        entry_price: Decimal,
        exit_price: Decimal,          # expected executable exit price
        size_shares: Decimal,
        fee_rate: Decimal = Decimal("0"),
        slippage: Decimal = Decimal("0"),
        spread: Decimal = Decimal("0"),
    ) -> dict:
        """Return net EV in USD and per-share, after fees/slippage/spread."""
        p_win = clamp(dec(p_win), Decimal("0"), Decimal("1"))
        p_loss = Decimal("1") - p_win
        entry_price = dec(entry_price)
        exit_price = dec(exit_price)
        size_shares = dec(size_shares)

        gross_payoff_per_share = exit_price - entry_price   # +1 / -0 per share on binary
        # Binary: win -> payout 1 per share, lose -> 0. Net per share = (p_win*1) - entry_price
        expected_gross_per_share = p_win * Decimal("1") - entry_price

        cost_per_share = dec(slippage) + dec(spread) / Decimal("2")
        fee_per_share = entry_price * dec(fee_rate)

        net_per_share = expected_gross_per_share - cost_per_share - fee_per_share
        net_ev_usd = net_per_share * size_shares

        return {
            "p_win": p_win,
            "expected_gross_per_share": expected_gross_per_share,
            "cost_per_share": cost_per_share,
            "fee_per_share": fee_per_share,
            "net_per_share": net_per_share,
            "net_ev_usd": net_ev_usd,
            "positive_expectancy": net_ev_usd > 0,
        }

    def fee_aware_size(
        self,
        *,
        available_usd: Decimal,
        risk_budget_usd: Decimal,
        entry_price: Decimal,
        fee_rate: Decimal,
        min_order_size_shares: Decimal,
    ) -> dict:
        """Max size such that worst-case loss (fees included) <= risk budget."""
        available_usd = dec(available_usd)
        risk_budget_usd = dec(risk_budget_usd)
        entry_price = dec(entry_price)
        fee_rate = dec(fee_rate)
        min_order_size_shares = dec(min_order_size_shares)

        # Worst case: lose the full entry cost (binary expires worthless).
        # cost per share = entry_price + fee.
        cost_per_share = entry_price * (Decimal("1") + fee_rate)
        max_shares_by_risk = safe_divide(risk_budget_usd, cost_per_share)
        max_shares_by_balance = safe_divide(available_usd, cost_per_share)
        max_shares = min(max_shares_by_risk, max_shares_by_balance)

        meets_minimum = max_shares >= min_order_size_shares
        return {
            "max_shares_by_risk": max_shares_by_risk,
            "max_shares_by_balance": max_shares_by_balance,
            "recommended_shares": max_shares if meets_minimum else Decimal("0"),
            "cost_per_share": cost_per_share,
            "meets_minimum_order": meets_minimum,
            "risk_usd": max_shares * cost_per_share,
        }

    def rank(self, decisions: list[EntryDecision]) -> list[EntryDecision]:
        """Rank by expected net value (descending). Rejects non-positive EV."""
        ranked = [d for d in decisions if d.eligible and (d.expected_net_value or Decimal("0")) > 0]
        ranked.sort(key=lambda d: d.expected_net_value or Decimal("0"), reverse=True)
        return ranked
