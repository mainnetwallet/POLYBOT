"""Portfolio exposure and risk engine.

Tracks filled positions, pending orders, partial fills, reserved capital,
entry fees, exposure, daily loss, available balance. Enforces:

- 2% max planned loss per trade
- 4% max daily loss
- max two consecutive losses + cooldown
- one active position, one entry per market
- no Martingale / no averaging down / no automatic size increase

Risk state persists across restarts via the database.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Optional

from polybot.clock import utc_now
from polybot.decimal_math import clamp, dec, safe_divide
from polybot.types import ExposureSnapshot, TradeSide

logger = logging.getLogger("polybot.risk")


@dataclass
class RiskCheck:
    allowed: bool
    reason: Optional[str]
    max_size_usd: Decimal
    reserved_usd: Decimal


class RiskEngine:
    def __init__(self, db, config) -> None:
        self.db = db
        self.config = config
        self._positions: dict = {}          # asset_id -> position
        self._pending_orders: dict = {}     # idempotency_key -> intent
        self._reserved_usd: Decimal = Decimal("0")
        self._daily_pnl_usd: Decimal = Decimal("0")
        self._daily_loss_usd: Decimal = Decimal("0")
        self._consecutive_losses: int = 0
        self._cooldown_until: Optional[datetime] = None
        self._daily_date = utc_now().date()
        self._restored = False

    # -- persistence / restore ------------------------------------------

    def restore(self) -> None:
        """Restore risk protections from the database (disaster recovery)."""
        row = self.db.fetchone(
            "SELECT daily_pnl_usd, daily_loss_usd, consecutive_losses, cooldown_until, "
            "available_balance_usd, allocated_capital_usd, reserved_capital_usd, exposure_usd, updated_at "
            "FROM risk_state WHERE id = 1"
        )
        if row:
            self._daily_pnl_usd = dec(row["daily_pnl_usd"])
            self._daily_loss_usd = dec(row["daily_loss_usd"])
            self._consecutive_losses = int(row["consecutive_losses"] or 0)
            self._cooldown_until = __import__("polybot.clock", fromlist=["parse_iso"]).parse_iso(row["cooldown_until"])
            self._reserved_usd = dec(row["reserved_capital_usd"])
        self._restored = True
        logger.info("Risk state restored: daily_loss=%s consecutive_losses=%s", self._daily_loss_usd, self._consecutive_losses)

    def _roll_date(self) -> None:
        today = utc_now().date()
        if today != self._daily_date:
            # New day: reset daily P&L/loss but keep consecutive losses unless
            # a cooldown is still active (cooldown is time-based, not day-based).
            self._daily_date = today
            self._daily_pnl_usd = Decimal("0")
            self._daily_loss_usd = Decimal("0")
            self._persist()

    def _persist(self) -> None:
        try:
            self.db.execute(
                """
                INSERT INTO risk_state(id, date, daily_pnl_usd, daily_loss_usd,
                    consecutive_losses, cooldown_until, available_balance_usd,
                    allocated_capital_usd, reserved_capital_usd, exposure_usd, updated_at)
                VALUES (1,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET
                    date=excluded.date, daily_pnl_usd=excluded.daily_pnl_usd,
                    daily_loss_usd=excluded.daily_loss_usd,
                    consecutive_losses=excluded.consecutive_losses,
                    cooldown_until=excluded.cooldown_until,
                    available_balance_usd=excluded.available_balance_usd,
                    allocated_capital_usd=excluded.allocated_capital_usd,
                    reserved_capital_usd=excluded.reserved_capital_usd,
                    exposure_usd=excluded.exposure_usd, updated_at=excluded.updated_at
                """,
                (
                    self._daily_date.isoformat(),
                    str(self._daily_pnl_usd),
                    str(self._daily_loss_usd),
                    self._consecutive_losses,
                    self._cooldown_until.isoformat() if self._cooldown_until else None,
                    str(dec("0")),   # available balance set externally
                    str(self.config.capital.allocated_usd or dec("0")),
                    str(self._reserved_usd),
                    str(self.exposure_usd()),
                    utc_now().isoformat(),
                ),
            )
        except Exception:
            logger.exception("Failed to persist risk state")

    # -- checks ----------------------------------------------------------

    def can_enter(
        self,
        *,
        available_usd: Decimal,
        planned_risk_usd: Decimal,
        active_positions: int = 0,
        pending_entries_for_market: int = 0,
    ) -> RiskCheck:
        self._roll_date()
        available_usd = dec(available_usd)
        planned_risk_usd = dec(planned_risk_usd)

        # One active position.
        if active_positions >= int(self.config.risk.max_active_positions):
            return RiskCheck(False, "maximum active positions reached", Decimal("0"), self._reserved_usd)

        # One entry per market.
        if pending_entries_for_market >= int(self.config.risk.max_entries_per_market):
            return RiskCheck(False, "maximum entries per market reached", Decimal("0"), self._reserved_usd)

        # Daily loss limit.
        daily_limit = dec(self.config.capital.allocated_usd or Decimal("0")) * dec(self.config.risk.daily_loss_limit)
        remaining_daily = daily_limit - self._daily_loss_usd
        if remaining_daily <= 0:
            return RiskCheck(False, "daily loss limit reached", Decimal("0"), self._reserved_usd)

        # Consecutive losses cooldown.
        if self._consecutive_losses >= int(self.config.risk.max_consecutive_losses):
            if self._cooldown_until is None or utc_now() < self._cooldown_until:
                return RiskCheck(False, "consecutive-loss cooldown active", Decimal("0"), self._reserved_usd)

        # 2% per-trade risk budget.
        risk_budget_usd = dec(self.config.capital.allocated_usd or Decimal("0")) * dec(self.config.risk.risk_per_trade)
        max_size_usd = min(planned_risk_usd, risk_budget_usd, remaining_daily, available_usd - self._reserved_usd)
        if max_size_usd <= 0:
            return RiskCheck(False, "insufficient capital for risk budget", Decimal("0"), self._reserved_usd)

        return RiskCheck(True, None, max_size_usd, self._reserved_usd)

    def reserve(self, reservation_id: str, usd: Decimal) -> None:
        self._reserved_usd += dec(usd)
        self._persist()

    def release(self, usd: Decimal) -> None:
        self._reserved_usd = max(Decimal("0"), self._reserved_usd - dec(usd))
        self._persist()

    def record_fill(self, cost_usd: Decimal, pnl_usd: Decimal, side: TradeSide, is_loss: bool) -> None:
        self._daily_pnl_usd += dec(pnl_usd)
        if is_loss:
            self._daily_loss_usd += abs(dec(pnl_usd))
            self._consecutive_losses += 1
            # Cooldown after consecutive losses.
            if self._consecutive_losses >= int(self.config.risk.max_consecutive_losses):
                from datetime import timedelta
                self._cooldown_until = utc_now() + timedelta(hours=24)
        else:
            self._consecutive_losses = 0
            self._cooldown_until = None
        self._persist()

    def daily_profit_lock(self) -> Optional[Decimal]:
        return self.config.risk.daily_profit_lock

    def exposure_usd(self) -> Decimal:
        return self._reserved_usd

    def snapshot(self, available_usd: Decimal, allocated_usd: Decimal) -> ExposureSnapshot:
        self._roll_date()
        allocated = dec(allocated_usd)
        daily_limit = allocated * dec(self.config.risk.daily_loss_limit)
        return ExposureSnapshot(
            timestamp=utc_now(),
            available_balance_usd=dec(available_usd),
            allocated_capital_usd=allocated,
            reserved_capital_usd=self._reserved_usd,
            exposure_usd=self.exposure_usd(),
            daily_pnl_usd=self._daily_pnl_usd,
            daily_loss_usd=self._daily_loss_usd,
            open_positions=len(self._positions),
            pending_orders=len(self._pending_orders),
            risk_budget_remaining_usd=max(allocated * dec(self.config.risk.risk_per_trade) - dec("0"), Decimal("0")),
            daily_loss_limit_remaining_usd=max(daily_limit - self._daily_loss_usd, Decimal("0")),
            within_limits=self._daily_loss_usd < daily_limit,
            blocking_reason=None if self._daily_loss_usd < daily_limit else "daily loss limit reached",
        )
