"""Exchange adapter base interface.

All exchange-specific (SDK) code lives in adapters. Trading logic depends only
on this interface and on the shared types in polybot.types. This makes it
possible to swap the SDK, run against a mock, or operate in PAPER mode.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from decimal import Decimal
from typing import Optional

from polybot.types import (
    MarketMetadata,
    MarketVersion,
    OrderSide,
    OrderType,
    Position,
)


class ExchangeAdapter(ABC):
    """Abstract exchange adapter for Polymarket."""

    @abstractmethod
    async def is_connected(self) -> bool:
        ...

    @abstractmethod
    async def discover_markets(
        self,
        query: str = "BTC",
        closed: bool = False,
        end_within_minutes: Optional[int] = None,
    ) -> list[MarketMetadata]:
        """Find active markets matching a query (e.g. BTC 5M)."""

    @abstractmethod
    async def get_market_metadata(self, market_id: str) -> Optional[MarketMetadata]:
        """Full metadata for a single market, including constraints and fees."""

    @abstractmethod
    async def get_order_book(
        self, asset_id: str
    ) -> Optional[dict]:
        """Current order book for an asset (bids/asks, tick, min size)."""

    @abstractmethod
    async def get_balance_usd(self) -> Decimal:
        """Available pUSD balance."""

    @abstractmethod
    async def check_allowance(self, asset_id: str, version: MarketVersion) -> bool:
        """Whether the exchange has allowance to trade this asset."""

    @abstractmethod
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
        """Submit an order. Returns a normalized result dict."""

    @abstractmethod
    async def cancel_order(self, order_id: str) -> bool:
        ...

    @abstractmethod
    async def cancel_all_orders(self) -> int:
        ...

    @abstractmethod
    async def get_position(self, asset_id: str) -> Optional[Position]:
        ...

    @abstractmethod
    async def get_open_orders(self) -> list[dict]:
        ...

    @abstractmethod
    async def settle_and_redeem(self, market_id: str) -> dict:
        ...

    @abstractmethod
    async def server_time(self) -> float:
        ...
