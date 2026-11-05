"""Dynamic position sizing.

Respects 2% risk budget, available pUSD, minimum order size, fees, slippage,
available liquidity, maximum order value, pending exposure and worst-case
settlement loss. Rounds DOWN to valid quantity precision. The risk manager
independently recomputes size and vetoes mismatches. No Martingale.
"""

from __future__ import annotations

import logging
from decimal import Decimal

from polybot.decimal_math import clamp, dec, qshare, safe_divide
from polybot.types import PositionSizingResult

logger = logging.getLogger("polybot.sizing")


class PositionSizer:
    def __init__(self, config) -> None:
        self.config = config

    def size(
        self,
        *,
        available_usd: Decimal,
        risk_budget_usd: Decimal,
        entry_price: Decimal,
        fee_rate: Decimal = Decimal("0"),
        slippage_usd_per_share: Decimal = Decimal("0"),
        min_order_size_shares: Optional[Decimal] = None,
        max_order_value_usd: Optional[Decimal] = None,
        liquidity_shares: Optional[Decimal] = None,
        pending_exposure_usd: Decimal = Decimal("0"),
    ) -> PositionSizingResult:
        available_usd = dec(available_usd)
        risk_budget_usd = dec(risk_budget_usd)
        entry_price = dec(entry_price)
        fee_rate = dec(fee_rate)
        slippage = dec(slippage_usd_per_share)
        min_order_size_shares = dec(min_order_size_shares) if min_order_size_shares is not None else Decimal("0")
        max_order_value_usd = dec(max_order_value_usd) if max_order_value_usd is not None else Decimal("0")
        liquidity_shares = dec(liquidity_shares) if liquidity_shares is not None else None
        pending_exposure_usd = dec(pending_exposure_usd)

        # Worst-case loss per share = full entry cost (binary can expire worthless).
        cost_per_share = entry_price + dec("0.002") + slippage  # entry + conservative fee + slippage

        # 2% risk budget.
        max_shares_by_risk = safe_divide(risk_budget_usd, cost_per_share)
        # Available balance after pending exposure.
        available_for_new = available_usd - pending_exposure_usd
        max_shares_by_balance = safe_divide(max(available_for_new, Decimal("0")), cost_per_share)

        max_shares = min(max_shares_by_risk, max_shares_by_balance)

        # Maximum order value cap.
        if max_order_value_usd > 0:
            max_shares_by_value = safe_divide(max_order_value_usd, entry_price)
            max_shares = min(max_shares, max_shares_by_value)

        # Liquidity cap.
        if liquidity_shares is not None:
            max_shares = min(max_shares, liquidity_shares)

        # Round DOWN to valid quantity precision.
        size_shares = qshare(max_shares)
        size_cost_usd = size_shares * cost_per_share
        risk_usd = size_cost_usd

        within_risk_budget = risk_usd <= risk_budget_usd
        meets_minimum = size_shares >= min_order_size_shares if min_order_size_shares > 0 else True

        veto_reason = None
        if not within_risk_budget:
            veto_reason = "exceeds risk budget"
        elif min_order_size_shares > 0 and not meets_minimum:
            veto_reason = "below minimum order size"

        return PositionSizingResult(
            size_shares=size_shares,
            size_cost_usd=size_cost_usd,
            risk_usd=risk_usd,
            max_allowed_by_risk=qshare(max_shares_by_risk),
            max_allowed_by_balance=qshare(max_shares_by_balance),
            min_order_size_usd=(min_order_size_shares * entry_price) if min_order_size_shares else Decimal("0"),
            within_risk_budget=within_risk_budget,
            meets_minimum_order=meets_minimum,
            veto_reason=veto_reason,
        )
