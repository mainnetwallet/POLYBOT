"""Order manager + persistent order journal.

Before any order is sent, the intent is persisted (idempotency key, market,
asset, side, price, size, strategy version, timestamp). Unknown order intents
are resolved on startup; never blindly retried.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from decimal import Decimal
from typing import Optional

from polybot.adapters.base import ExchangeAdapter
from polybot.clock import utc_now
from polybot.decimal_math import dec
from polybot.idempotency import generate_idempotency_key, IdempotencyKey
from polybot.types import (
    OrderIntentStatus,
    OrderJournalEntry,
    OrderSide,
    OrderType,
    TradeSide,
)

logger = logging.getLogger("polybot.order_manager")


class OrderManager:
    def __init__(self, db, adapter: ExchangeAdapter, strategy_version: str = "1.0.0") -> None:
        self.db = db
        self.adapter = adapter
        self.strategy_version = strategy_version
        self._lock = asyncio.Lock()
        self._pending: dict[str, OrderJournalEntry] = {}

    # -- journal persistence (before submission) ------------------------

    async def journal_intent(
        self,
        *,
        market_id: str,
        condition_id: Optional[str],
        asset_id: str,
        side: TradeSide,
        price: Decimal,
        size_shares: Decimal,
        order_type: OrderType,
    ) -> IdempotencyKey:
        key = generate_idempotency_key(
            market_id=market_id,
            asset_id=asset_id,
            side=side.value,
            price=str(price),
            size=str(size_shares),
            strategy_version=self.strategy_version,
        )
        entry = OrderJournalEntry(
            idempotency_key=key.key,
            market_id=market_id,
            condition_id=condition_id,
            asset_id=asset_id,
            side=side,
            price=dec(price),
            size_shares=dec(size_shares),
            order_type=order_type,
            strategy_version=self.strategy_version,
            timestamp=utc_now(),
            status=OrderIntentStatus.PENDING,
        )
        self._persist(entry)
        self._pending[key.key] = entry
        logger.info("Journalled order intent %s %s %s @ %s", key.key[-8:], side.value, asset_id, price)
        return key

    def _persist(self, entry: OrderJournalEntry) -> None:
        self.db.execute(
            """
            INSERT INTO orders(idempotency_key, market_id, condition_id, asset_id, side,
                price, size_shares, order_type, strategy_version, timestamp, status, order_id,
                filled_shares, rejection_reason)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(idempotency_key) DO UPDATE SET
                status=excluded.status, order_id=excluded.order_id,
                filled_shares=excluded.filled_shares, rejection_reason=excluded.rejection_reason
            """,
            (
                entry.idempotency_key,
                entry.market_id,
                entry.condition_id,
                entry.asset_id,
                entry.side.value,
                str(entry.price),
                str(entry.size_shares),
                entry.order_type.value,
                entry.strategy_version,
                entry.timestamp.isoformat(),
                entry.status.value,
                entry.order_id,
                str(entry.filled_shares),
                entry.rejection_reason,
            ),
        )

    # -- submission -----------------------------------------------------

    async def submit(
        self,
        *,
        key: IdempotencyKey,
        market_id: str,
        asset_id: str,
        version,
        side: TradeSide,
        price: Optional[Decimal],
        size_shares: Decimal,
        order_type: OrderType,
        max_spend_usd: Optional[Decimal] = None,
    ) -> dict:
        entry = self._pending.get(key.key)
        if entry is None:
            raise KeyError(f"no journaled intent for {key.key}")
        if entry.status != OrderIntentStatus.PENDING:
            return {"ok": False, "error": f"intent already {entry.status.value}", "idempotency_key": key.key}

        entry.status = OrderIntentStatus.SUBMITTED
        self._persist(entry)

        try:
            result = await self.adapter.place_order(
                market_id=market_id,
                asset_id=asset_id,
                version=version,
                side=side,
                price=price,
                size_shares=size_shares,
                order_type=order_type,
                max_spend_usd=max_spend_usd,
            )
        except Exception as exc:  # pragma: no cover - network
            entry.status = OrderIntentStatus.UNKNOWN
            entry.rejection_reason = str(exc)
            self._persist(entry)
            logger.exception("Order submission raised; intent marked UNKNOWN %s", key.key[-8:])
            return {"ok": False, "error": str(exc), "idempotency_key": key.key, "unknown": True}

        # Update journal from response.
        entry.order_id = result.get("order_id")
        entry.status = OrderIntentStatus.ACKNOWLEDGED if result.get("ok") else OrderIntentStatus.REJECTED
        entry.rejection_reason = result.get("error") or result.get("message")
        if result.get("trade_ids"):
            entry.status = OrderIntentStatus.FILLED
            entry.filled_shares = entry.size_shares
        self._persist(entry)
        result["idempotency_key"] = key.key
        result["simulated"] = result.get("simulated", False)
        logger.info(
            "Order %s -> ok=%s status=%s id=%s",
            key.key[-8:], result.get("ok"), entry.status.value, entry.order_id,
        )
        return result

    # -- cancellation ---------------------------------------------------

    async def cancel(self, order_id: str) -> bool:
        ok = await self.adapter.cancel_order(order_id)
        # Mark journaled entries referencing this order cancelled.
        for entry in self._pending.values():
            if entry.order_id == order_id:
                entry.status = OrderIntentStatus.CANCELLED
                self._persist(entry)
        return ok

    # -- reconciliation -------------------------------------------------

    async def reconcile(self, open_orders: list[dict]) -> None:
        """Reconcile journal with actual open orders. Mismatches block new entries."""
        open_ids = {o.get("order_id") for o in open_orders if o.get("order_id")}
        for key, entry in list(self._pending.items()):
            if entry.status in (OrderIntentStatus.FILLED, OrderIntentStatus.CANCELLED, OrderIntentStatus.REJECTED):
                continue
            if entry.order_id and entry.order_id not in open_ids:
                # Order disappeared from the book -> unknown intent.
                entry.status = OrderIntentStatus.UNKNOWN
                entry.reconciliation_notes = "order not in open orders after submission"
                self._persist(entry)
                logger.warning("Unknown order intent %s during reconciliation", key.key[-8:])

    def pending_unknown(self) -> list[OrderJournalEntry]:
        return [e for e in self._pending.values() if e.status == OrderIntentStatus.UNKNOWN]

    def all_entries(self) -> list[OrderJournalEntry]:
        return list(self._pending.values())

    async def load_from_db(self) -> None:
        """Reload journaled intents on startup (disaster recovery)."""
        rows = self.db.fetchall("SELECT * FROM orders WHERE status IN ('PENDING','SUBMITTED','ACKNOWLEDGED','UNKNOWN')")
        for row in rows:
            entry = OrderJournalEntry(
                idempotency_key=row["idempotency_key"],
                market_id=row["market_id"],
                condition_id=row["condition_id"],
                asset_id=row["asset_id"],
                side=TradeSide(row["side"]),
                price=dec(row["price"]),
                size_shares=dec(row["size_shares"]),
                order_type=OrderType(row["order_type"]),
                strategy_version=row["strategy_version"],
                timestamp=__import__("polybot.clock", fromlist=["parse_iso"]).parse_iso(row["timestamp"]),
                status=OrderIntentStatus(row["status"]),
                order_id=row["order_id"],
                filled_shares=dec(row["filled_shares"] or "0"),
                rejection_reason=row["rejection_reason"],
                reconciliation_notes=row["reconciliation_notes"],
            )
            self._pending[entry.idempotency_key] = entry
        logger.info("Loaded %d pending order intents from journal", len(self._pending))
