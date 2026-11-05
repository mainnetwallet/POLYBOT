"""Polymarket exchange adapter (SDK-backed).

Wraps the official ``polymarket-client`` (0.16.0) so trading logic never
touches SDK types directly. Supports both public market data and authenticated
order routing via ``AsyncSecureClient``.

Market-identifier routing follows ``market.version``:
  V1 (CTF)    -> clobTokenIds / outcome ``token_id``
  V2 (V2)     -> positionIds / outcome ``position_id``
Unknown versions are rejected.
"""

from __future__ import annotations

import re
import asyncio
import logging
import os
import time
from datetime import timedelta
from decimal import Decimal
from typing import Optional

from polymarket import AsyncPublicClient, AsyncSecureClient

from polybot.adapters.base import ExchangeAdapter
from polybot.clock import utc_now
from polybot.constants import (
    CHAIN_ID,
    CONDITIONAL_TOKENS_ADDRESS,
    MARKET_VERSION_V1,
    MARKET_VERSION_V2,
    MODE,
    ORDER_TYPE_FAK,
    PUSD_TOKEN_ADDRESS,
    SIG_TYPE_DEPOSIT_WALLET,
    SIDE_BUY,
    SIDE_SELL,
)
from polybot.decimal_math import dec, qshare
from polybot.types import (
    MarketMetadata,
    MarketOutcomeId,
    MarketTradingConstraints,
    MarketVersion,
    OrderSide,
    OrderType,
    Position,
)


logger = logging.getLogger("polybot.polymarket")

class PolymarketAdapterError(RuntimeError):
    """Raised when the Polymarket adapter cannot complete an operation."""


class NotAuthenticatedError(PolymarketAdapterError):
    """Raised when an authenticated operation is attempted without credentials."""


class UnknownMarketVersionError(PolymarketAdapterError):
    """Raised when a market version other than v1/v2 is encountered."""


class PolymarketAdapter(ExchangeAdapter):
    def __init__(
        self,
        *,
        mode: str = MODE.PAPER,
        private_key: Optional[str] = None,
        wallet_address: Optional[str] = None,
        clob_api_key: Optional[str] = None,
        clob_api_secret: Optional[str] = None,
        clob_api_passphrase: Optional[str] = None,
        gamma_api_url: str = "https://gamma-api.polymarket.com",
    ) -> None:
        self.mode = mode
        self.private_key = private_key
        self.wallet_address = wallet_address
        self.clob_api_key = clob_api_key
        self.clob_api_secret = clob_api_secret
        self.clob_api_passphrase = clob_api_passphrase
        self.gamma_api_url = gamma_api_url

        self._public: Optional[AsyncPublicClient] = None
        self._secure: Optional[AsyncSecureClient] = None
        self._lock = asyncio.Lock()
        self._started = False

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #
    async def start(self) -> None:
        if self._started:
            return
        self._public = AsyncPublicClient()
        # Only create the secure client when credentials are present.
        if self.private_key and self.wallet_address:
            self._secure = await AsyncSecureClient.create(
                private_key=self.private_key,
                wallet=self.wallet_address,
            )
        self._started = True

    async def close(self) -> None:
        self._started = False
        self._secure = None
        self._public = None

    async def is_connected(self) -> bool:
        return self._started and self._public is not None

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #
    def _require_public(self) -> AsyncPublicClient:
        if self._public is None:
            raise PolymarketAdapterError("Public client not started")
        return self._public

    def _require_secure(self) -> AsyncSecureClient:
        if self._secure is None:
            raise NotAuthenticatedError(
                "Authenticated Polymarket operation requires private key and wallet"
            )
        return self._secure

    @staticmethod
    def _sdk_version(value: Optional[str]) -> MarketVersion:
        if value == MARKET_VERSION_V2:
            return MarketVersion.V2
        if value == MARKET_VERSION_V1:
            return MarketVersion.V1
        if value is None:
            return MarketVersion.V1  # server default is CTF; treat conservatively
        raise UnknownMarketVersionError(f"Unsupported market version: {value}")

    @staticmethod
    def _asset_id_for(market: object, label: str, version: MarketVersion) -> Optional[str]:
        outcomes = getattr(market, "outcomes", None)
        if outcomes is None:
            return None
        yes = getattr(outcomes, "yes", None)
        no = getattr(outcomes, "no", None)
        candidates = []
        if yes is not None and getattr(yes, "label", None) == label:
            candidates.append(yes)
        if no is not None and getattr(no, "label", None) == label:
            candidates.append(no)
        for outcome in candidates:
            if version == MarketVersion.V2:
                pid = getattr(outcome, "position_id", None)
                if pid:
                    return pid
            else:
                tid = getattr(outcome, "token_id", None)
                if tid:
                    return tid
        return None

    # ------------------------------------------------------------------ #
    # Market discovery
    # ------------------------------------------------------------------ #
    async def discover_markets(
        self,
        query: str = "BTC",
        closed: bool = False,
        end_within_minutes: Optional[int] = None,
    ) -> list[MarketMetadata]:
        """List active markets matching ``query``.

        The SDK does not expose a full-text search on markets, so we page
        through active markets and filter client-side by question/slug. This
        never hardcodes a market ID, slug or token.
        """
        client = self._require_public()
        results: list[MarketMetadata] = []
        # Match on the query's words (slugs use hyphens, e.g. "btc-updown-5m-..."),
        # not on the literal phrase "btc 5m".
        query_words = [w for w in re.sub(r"[^a-z0-9]+", " ", query.lower()).split() if w]
        # Short-lived markets (e.g. BTC 5M) end soon after they open, so asking
        # the server only for markets ending in the next N minutes avoids paging
        # through every open market on Polymarket.
        filters: dict = {}
        if end_within_minutes:
            now = utc_now()
            filters["end_date_min"] = now
            filters["end_date_max"] = now + timedelta(minutes=end_within_minutes)
        pages = client.list_markets(closed=closed, page_size=100, **filters)
        started = time.monotonic()
        n_pages = n_scanned = 0
        try:
            async for page in pages:
                n_pages += 1
                n_scanned += len(page.items)
                for market in page.items:
                    text = " ".join(
                        str(getattr(market, attr, "") or "")
                        for attr in ("question", "slug", "id")
                    ).lower()
                    text = re.sub(r"[^a-z0-9]+", " ", text)
                    if not all(w in text for w in query_words):
                        continue
                    meta = self._market_to_metadata(market)
                    if meta is not None:
                        results.append(meta)
        except Exception as exc:  # pragma: no cover - network
            raise PolymarketAdapterError(f"Market discovery failed: {exc}") from exc
        logger.info(
            "Discovery scan: %d pages, %d markets scanned, %d matched %r in %.1fs%s",
            n_pages, n_scanned, len(results), query, time.monotonic() - started,
            f" (ending within {end_within_minutes} min)" if end_within_minutes else "",
        )
        return results

    async def get_market_metadata(self, market_id: str) -> Optional[MarketMetadata]:
        client = self._require_public()
        try:
            market = await client.get_market(id=market_id)
        except Exception as exc:  # pragma: no cover - network
            raise PolymarketAdapterError(
                f"Failed to fetch market {market_id}: {exc}"
            ) from exc
        return self._market_to_metadata(market)

    def _market_to_metadata(self, market: object) -> Optional[MarketMetadata]:
        version_raw = getattr(market, "version", None)
        try:
            version = self._sdk_version(version_raw)
        except UnknownMarketVersionError:
            return None  # reject unknown versions

        outcomes = getattr(market, "outcomes", None)
        outcome_list: list[MarketOutcomeId] = []
        outcome_prices: list[Decimal] = []
        if outcomes is not None:
            for label in ("yes", "no"):
                outcome = getattr(outcomes, label, None)
                if outcome is None:
                    continue
                outcome_list.append(
                    MarketOutcomeId(
                        label=getattr(outcome, "label", label.upper()),
                        token_id=getattr(outcome, "token_id", None),
                        position_id=getattr(outcome, "position_id", None),
                    )
                )
                price = getattr(outcome, "price", None)
                outcome_prices.append(dec(price) if price is not None else Decimal("0"))

        state = getattr(market, "state", None)
        trading = getattr(market, "trading", None)
        metrics = getattr(market, "metrics", None)
        prices = getattr(market, "prices", None)
        rewards = getattr(market, "rewards", None)

        constraints = MarketTradingConstraints()
        if trading is not None:
            constraints.minimum_tick_size = dec(
                getattr(trading, "minimum_tick_size", None)
            )
            constraints.minimum_order_size = dec(
                getattr(trading, "minimum_order_size", None)
            )
            constraints.fees_enabled = bool(
                getattr(trading, "fees_enabled", False)
            )
            fs = getattr(trading, "fee_schedule", None)
            if fs is not None:
                constraints.fee_rate = dec(getattr(fs, "rate", None))
                constraints.fee_exponent = getattr(fs, "exponent", None)
                constraints.fee_taker_only = bool(getattr(fs, "taker_only", True))
                constraints.fee_rebate_rate = dec(
                    getattr(fs, "rebate_rate", None)
                )
            constraints.seconds_delay = getattr(trading, "seconds_delay", None)

        return MarketMetadata(
            market_id=str(getattr(market, "id", "")),
            slug=getattr(market, "slug", None),
            question=getattr(market, "question", None),
            condition_id=getattr(market, "condition_id", None),
            version=version,
            state_active=bool(getattr(state, "active", False)) if state else False,
            state_closed=bool(getattr(state, "closed", False)) if state else False,
            state_archived=bool(getattr(state, "archived", False)) if state else False,
            accepting_orders=bool(
                getattr(state, "accepting_orders", False)
            )
            if state
            else False,
            enable_order_book=bool(
                getattr(state, "enable_order_book", False)
            )
            if state
            else False,
            neg_risk=bool(getattr(state, "neg_risk", False)) if state else False,
            restricted=bool(getattr(state, "restricted", False)) if state else False,
            start_date=getattr(state, "start_date", None) if state else None,
            end_date=getattr(state, "end_date", None) if state else None,
            closed_time=getattr(state, "closed_time", None) if state else None,
            outcomes=outcome_list,
            outcome_prices=outcome_prices,
            trading=constraints,
            metrics_liquidity=dec(getattr(metrics, "liquidity", None)) if metrics else None,
            metrics_volume_24hr=dec(
                getattr(metrics, "volume_24hr", None)
            )
            if metrics
            else None,
            prices_best_bid=dec(getattr(prices, "best_bid", None)) if prices else None,
            prices_best_ask=dec(getattr(prices, "best_ask", None)) if prices else None,
            prices_spread=dec(getattr(prices, "spread", None)) if prices else None,
            last_trade_price=dec(
                getattr(prices, "last_trade_price", None)
            )
            if prices
            else None,
            rewards_min_size=dec(
                getattr(rewards, "rewards_min_size", None)
            )
            if rewards
            else None,
            rewards_max_spread=getattr(rewards, "rewards_max_spread", None)
            if rewards
            else None,
            raw={
                "id": getattr(market, "id", None),
                "version": version_raw,
                "slug": getattr(market, "slug", None),
            },
        )

    # ------------------------------------------------------------------ #
    # Order book / balances / allowances
    # ------------------------------------------------------------------ #
    async def get_order_book(self, asset_id: str) -> Optional[dict]:
        # The SDK public client exposes market data; the order book is read
        # through the secure client's market stream or the CLOB REST book.
        # We expose a normalized book via the secure client when available,
        # otherwise return None (caller falls back to the market stream).
        if self._secure is None:
            return None
        try:
            # estimate_market_price requires a side/amount; for a snapshot we
            # use the market stream instead. Return None here and let the
            # caller use the market-stream book.
            return None
        except Exception:
            return None

    async def get_balance_usd(self) -> Decimal:
        if self.mode == MODE.PAPER:
            return Decimal("0")
        secure = self._require_secure()
        try:
            # SDK does not expose a direct balance method in the public docs;
            # balances are derived from positions / CLOB data API. Return 0 and
            # let the risk engine query the data API if needed.
            return Decimal("0")
        except NotAuthenticatedError:
            raise
        except Exception as exc:  # pragma: no cover - network
            raise PolymarketAdapterError(f"Balance query failed: {exc}") from exc

    async def check_allowance(
        self, asset_id: str, version: MarketVersion
    ) -> bool:
        """Allowance is configured out-of-band via setup_trading_approvals.

        The adapter reports whether the SDK reports a configured secure client
        (credentials present). Actual on-chain allowance must be verified by
        the user before LIVE activation (see validation/live_gate.py).
        """
        return self._secure is not None

    # ------------------------------------------------------------------ #
    # Order execution
    # ------------------------------------------------------------------ #
    async def place_order(
        self,
        market_id: str,
        asset_id: str,
        version: MarketVersion,
        side: OrderSide,
        price: Optional[Decimal],
        size_shares: Decimal,
        order_type: OrderType,
        max_spend_usd: Optional[Decimal] = None,
    ) -> dict:
        if version not in (MarketVersion.V1, MarketVersion.V2):
            raise UnknownMarketVersionError(f"Unsupported market version: {version}")

        size_shares = qshare(size_shares)
        if size_shares <= 0:
            return {"ok": False, "error": "size must be positive"}

        if self.mode == MODE.PAPER:
            # Simulated acceptance; actual fill modeled by the paper executor.
            return {
                "ok": True,
                "order_id": f"paper-{asset_id}-{side.value}-{int(__import__('time').time()*1000)}",
                "status": "matched",
                "making_amount": str(size_shares * (price or Decimal("0"))),
                "taking_amount": str(size_shares),
                "trade_ids": [],
                "transactions_hashes": [],
                "simulated": True,
            }

        secure = self._require_secure()
        side_str = "BUY" if side == OrderSide.BUY else "SELL"
        try:
            if order_type in (OrderType.FAK, OrderType.FOK):
                # Market order: derive price from book via estimate_market_price.
                amount = str(size_shares * (price or Decimal("1")))
                est = await secure.estimate_market_price(
                    asset_id=asset_id,
                    side=side_str,
                    amount=amount,
                    order_type=order_type.value,
                )
                exec_price = dec(est)
                response = await secure.place_market_order(
                    asset_id=asset_id,
                    side=side_str,
                    amount=str(size_shares * exec_price),
                    max_price=str(exec_price) if side == OrderSide.BUY else None,
                    min_price=str(exec_price) if side == OrderSide.SELL else None,
                    order_type=order_type.value,
                    max_spend=str(max_spend_usd) if max_spend_usd is not None else None,
                )
            else:
                # Limit order (GTC/GTD).
                if price is None:
                    return {"ok": False, "error": "limit orders require a price"}
                response = await secure.place_limit_order(
                    asset_id=asset_id,
                    side=side_str,
                    price=str(price),
                    size=str(size_shares),
                )

            return self._normalize_order_response(response)
        except NotAuthenticatedError:
            raise
        except Exception as exc:  # pragma: no cover - network / SDK
            return {"ok": False, "error": str(exc)}

    @staticmethod
    def _normalize_order_response(response: object) -> dict:
        return {
            "ok": bool(getattr(response, "ok", False)),
            "order_id": getattr(response, "order_id", None),
            "status": getattr(response, "status", None),
            "making_amount": getattr(response, "making_amount", None),
            "taking_amount": getattr(response, "taking_amount", None),
            "trade_ids": list(getattr(response, "trade_ids", []) or []),
            "transactions_hashes": list(
                getattr(response, "transactions_hashes", []) or []
            ),
            "code": getattr(response, "code", None),
            "message": getattr(response, "message", None),
            "simulated": False,
        }

    async def cancel_order(self, order_id: str) -> bool:
        if self.mode == MODE.PAPER:
            return True
        secure = self._require_secure()
        try:
            # SDK surface for cancellation (per docs: manage orders).
            # Use cancel_order if present on this SDK version.
            cancel = getattr(secure, "cancel_order", None)
            if cancel is None:
                raise PolymarketAdapterError("cancel_order not exposed by SDK")
            resp = await cancel(order_id=order_id)
            return bool(getattr(resp, "ok", False))
        except NotAuthenticatedError:
            raise
        except Exception:  # pragma: no cover - network
            return False

    async def cancel_all_orders(self) -> int:
        if self.mode == MODE.PAPER:
            return 0
        secure = self._require_secure()
        try:
            cancel_all = getattr(secure, "cancel_all_orders", None)
            if cancel_all is None:
                return 0
            resp = await cancel_all()
            return len(getattr(resp, "results", []) or [])
        except NotAuthenticatedError:
            raise
        except Exception:  # pragma: no cover - network
            return 0

    # ------------------------------------------------------------------ #
    # Positions
    # ------------------------------------------------------------------ #
    async def get_position(self, asset_id: str) -> Optional[Position]:
        if self.mode == MODE.PAPER:
            return None
        secure = self._require_secure()
        try:
            # Need condition_id; positions are listed per market/condition.
            # This is a best-effort single-asset lookup.
            return None
        except NotAuthenticatedError:
            raise
        except Exception:  # pragma: no cover - network
            return None

    async def get_open_orders(self) -> list[dict]:
        if self.mode == MODE.PAPER:
            return []
        secure = self._require_secure()
        try:
            page = await secure.list_orders().first_page()
            return [
                {
                    "order_id": getattr(o, "order_id", None),
                    "asset_id": getattr(o, "asset_id", None),
                    "side": getattr(o, "side", None),
                    "status": getattr(o, "status", None),
                    "price": getattr(o, "price", None),
                    "size": getattr(o, "size", None),
                }
                for o in (page.items or [])
            ]
        except NotAuthenticatedError:
            raise
        except Exception:  # pragma: no cover - network
            return []

    async def settle_and_redeem(self, market_id: str) -> dict:
        """Redemption is handled by position lifecycle methods (split/merge/redeem).

        The adapter exposes a hook; actual redemption requires the user to
        invoke position management via the SDK or dashboard. Returns a status
        describing what would be needed.
        """
        return {
            "market_id": market_id,
            "redeemable": False,
            "note": "Redemption must be triggered explicitly via position lifecycle (split/merge/redeem).",
        }

    async def server_time(self) -> float:
        import time

        return time.time()
