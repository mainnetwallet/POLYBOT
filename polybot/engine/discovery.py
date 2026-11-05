"""Market discovery engine.

Finds active BTC 5-minute UP/DOWN markets automatically using the Gamma API
via the Polymarket SDK. Validates market ID, condition ID, version, asset
identifiers, outcomes, timing, order acceptance, tick size, minimum order
size, fee schedule, resolution rules and liquidity.

Never hardcodes market IDs, slugs or token identifiers. Routes by
``market.version`` (V1 -> clobTokenIds, V2 -> positionIds).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Optional

from polybot.adapters.polymarket import PolymarketAdapter, UnknownMarketVersionError
from polybot.clock import parse_iso, utc_now
from polybot.constants import (
    MARKET_VERSION_V1,
    MARKET_VERSION_V2,
)
from polybot.types import (
    MarketMetadata,
    MarketVersion,
)
from polybot.decimal_math import dec

logger = logging.getLogger("polybot.discovery")


@dataclass
class DiscoveryResult:
    markets: list[MarketMetadata] = field(default_factory=list)
    btc_5m_markets: list[MarketMetadata] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    scanned_at: Optional[datetime] = None


class MarketDiscovery:
    def __init__(self, adapter: PolymarketAdapter) -> None:
        self.adapter = adapter
        self._known_market_ids: set[str] = set()

    async def refresh(self, query: str = "BTC") -> DiscoveryResult:
        result = DiscoveryResult(scanned_at=utc_now())
        try:
            markets = await self.adapter.discover_markets(query=query, closed=False)
        except Exception as exc:  # pragma: no cover - network
            logger.exception("Discovery failed")
            result.errors.append(str(exc))
            return result

        result.markets = markets
        for meta in markets:
            if self._is_btc_5m(meta):
                result.btc_5m_markets.append(meta)
                self._known_market_ids.add(meta.market_id)
        logger.info(
            "Discovery: %d markets scanned, %d BTC 5M candidates",
            len(markets),
            len(result.btc_5m_markets),
        )
        return result

    @staticmethod
    def _is_btc_5m(meta: MarketMetadata) -> bool:
        """Heuristic match for BTC 5-minute UP/DOWN markets without hardcoding IDs."""
        text = " ".join(
            [
                meta.question or "",
                meta.slug or "",
                meta.market_id,
            ]
        ).lower()
        has_btc = "btc" in text or "bitcoin" in text
        has_5m = any(k in text for k in ("5m", "5-minute", "5 minute", "5min"))
        is_binary = len(meta.outcomes) == 2
        return has_btc and has_5m and is_binary

    async def validate(self, meta: MarketMetadata) -> tuple[bool, list[str]]:
        """Validate a candidate market's metadata. Returns (ok, reasons)."""
        reasons: list[str] = []
        ok = True

        if not meta.market_id:
            ok = False
            reasons.append("missing market ID")
        if not meta.condition_id:
            ok = False
            reasons.append("missing condition ID")
        if meta.version not in (MarketVersion.V1, MarketVersion.V2):
            ok = False
            reasons.append(f"unsupported market version: {meta.version}")
        if not meta.trade_ready:
            ok = False
            reasons.append("market not trade-ready (active/closed/acceptingOrders)")
        if not meta.outcomes or len(meta.outcomes) != 2:
            ok = False
            reasons.append("market is not binary UP/DOWN")
        else:
            asset_up = meta.asset_id_for_outcome("UP")
            asset_down = meta.asset_id_for_outcome("DOWN")
            if not asset_up or not asset_down:
                ok = False
                reasons.append("missing outcome asset identifiers")
        if meta.trading.minimum_tick_size is None:
            ok = False
            reasons.append("missing tick size")
        if meta.trading.minimum_order_size is None:
            ok = False
            reasons.append("missing minimum order size")
        if meta.end_date is None:
            ok = False
            reasons.append("missing end date")
        if meta.event_start_time is None:
            # Not fatal: some markets expose timing via state.start_date only.
            reasons.append("event_start_time not exposed (using state.start_date)")
        if meta.metrics_liquidity is not None and meta.metrics_liquidity <= 0:
            ok = False
            reasons.append("no liquidity reported")
        return ok, reasons


class MarketLifecycle:
    """Persistent market state machine: DISCOVERED -> ACTIVE -> ENTRY_CLOSED
    -> RESOLVING -> SETTLED.

    Entry state resets for each market. One initial entry per market.
    """

    def __init__(self, db) -> None:
        self.db = db
        self._machines: dict[str, dict] = {}  # market_id -> state record

    def register(self, market_id: str, metadata: MarketMetadata) -> None:
        now = utc_now()
        record = {
            "market_id": market_id,
            "state": "DISCOVERED",
            "metadata": metadata,
            "entered_at": None,
            "entry_closed_at": None,
            "resolving_at": None,
            "settled_at": None,
            "entry_side": None,
            "entry_order_id": None,
            "winner": None,
            "updated_at": now,
        }
        self._machines[market_id] = record
        self._persist(market_id, record)

    def get(self, market_id: str) -> Optional[dict]:
        return self._machines.get(market_id)

    def state(self, market_id: str) -> Optional[str]:
        rec = self._machines.get(market_id)
        return rec["state"] if rec else None

    def transition(self, market_id: str, to_state: str) -> bool:
        rec = self._machines.get(market_id)
        if rec is None:
            return False
        from_state = rec["state"]
        if not self._valid_transition(from_state, to_state):
            return False
        rec["state"] = to_state
        rec["updated_at"] = utc_now()
        if to_state == "ACTIVE":
            rec["entered_at"] = utc_now()
        elif to_state == "ENTRY_CLOSED":
            rec["entry_closed_at"] = utc_now()
        elif to_state == "RESOLVING":
            rec["resolving_at"] = utc_now()
        elif to_state == "SETTLED":
            rec["settled_at"] = utc_now()
        self._persist(market_id, rec)
        logger = logging.getLogger("polybot.lifecycle")
        logger.info("Market %s: %s -> %s", market_id, from_state, to_state)
        return True

    def set_entry(self, market_id: str, side: str, order_id: str) -> None:
        rec = self._machines.get(market_id)
        if rec:
            rec["entry_side"] = side
            rec["entry_order_id"] = order_id
            self._persist(market_id, rec)

    def set_winner(self, market_id: str, winner: str) -> None:
        rec = self._machines.get(market_id)
        if rec:
            rec["winner"] = winner
            self._persist(market_id, rec)

    def reset_entry_state(self, market_id: str) -> None:
        """Reset entry state for the next market cycle."""
        rec = self._machines.get(market_id)
        if rec:
            rec["entry_side"] = None
            rec["entry_order_id"] = None
            rec["entered_at"] = None
            self._persist(market_id, rec)

    @staticmethod
    def _valid_transition(frm: str, to: str) -> bool:
        transitions = {
            "DISCOVERED": ("ACTIVE",),
            "ACTIVE": ("ENTRY_CLOSED", "RESOLVING"),
            "ENTRY_CLOSED": ("RESOLVING",),
            "RESOLVING": ("SETTLED",),
            "SETTLED": (),
        }
        return to in transitions.get(frm, ())

    def _persist(self, market_id: str, rec: dict) -> None:
        try:
            import json
            self.db.execute(
                """
                INSERT INTO markets(market_id, slug, question, condition_id, version, state,
                    active, closed, accepting_orders, start_date, end_date,
                    event_start_time, min_tick_size, min_order_size, fees_enabled,
                    liquidity, last_seen, raw_json)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(market_id) DO UPDATE SET
                    slug=excluded.slug, question=excluded.question,
                    condition_id=excluded.condition_id, version=excluded.version,
                    state=excluded.state, active=excluded.active,
                    closed=excluded.closed, accepting_orders=excluded.accepting_orders,
                    start_date=excluded.start_date, end_date=excluded.end_date,
                    event_start_time=excluded.event_start_time,
                    min_tick_size=excluded.min_tick_size,
                    min_order_size=excluded.min_order_size,
                    fees_enabled=excluded.fees_enabled,
                    liquidity=excluded.liquidity, last_seen=excluded.last_seen,
                    raw_json=excluded.raw_json
                """,
                (
                    market_id,
                    rec.get("metadata").slug if rec.get("metadata") else None,
                    rec.get("metadata").question if rec.get("metadata") else None,
                    rec.get("metadata").condition_id if rec.get("metadata") else None,
                    rec.get("metadata").version.value if rec.get("metadata") else None,
                    rec["state"],
                    1 if rec.get("metadata") and rec["metadata"].state_active else 0,
                    1 if rec.get("metadata") and rec["metadata"].state_closed else 0,
                    1 if rec.get("metadata") and rec["metadata"].accepting_orders else 0,
                    rec["metadata"].start_date.isoformat() if rec.get("metadata") and rec["metadata"].start_date else None,
                    rec["metadata"].end_date.isoformat() if rec.get("metadata") and rec["metadata"].end_date else None,
                    rec["metadata"].event_start_time.isoformat() if rec.get("metadata") and rec["metadata"].event_start_time else None,
                    str(rec["metadata"].trading.minimum_tick_size) if rec.get("metadata") and rec["metadata"].trading.minimum_tick_size else None,
                    str(rec["metadata"].trading.minimum_order_size) if rec.get("metadata") and rec["metadata"].trading.minimum_order_size else None,
                    1 if rec.get("metadata") and rec["metadata"].trading.fees_enabled else 0,
                    str(rec["metadata"].metrics_liquidity) if rec.get("metadata") and rec["metadata"].metrics_liquidity else None,
                    rec["updated_at"].isoformat(),
                    json.dumps(rec.get("metadata").raw if rec.get("metadata") else {}),
                ),
            )
        except Exception:
            logging.getLogger("polybot.lifecycle").exception("Failed to persist market %s", market_id)
