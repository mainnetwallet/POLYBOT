"""Pytest fixtures and mocks for POLYBOT tests.

All Polymarket SDK/API surfaces are mocked. No real funds or network
access is used.
"""

from __future__ import annotations

import asyncio
import sqlite3
import tempfile
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional
from unittest.mock import AsyncMock, MagicMock

import pytest

from polybot.config import Settings
from polybot.constants import MARKET_VERSION_V1, MARKET_VERSION_V2, MODE
from polybot.database import Database
from polybot.decimal_math import dec
from polybot.types import (
    MarketMetadata,
    MarketOutcomeId,
    MarketTradingConstraints,
    MarketVersion,
    OrderSide,
    OrderType,
    TradeSide,
)
from polybot.adapters.base import ExchangeAdapter


# --------------------------------------------------------------------------- #
# Fake market metadata
# --------------------------------------------------------------------------- #
def _make_metadata(version: str = MARKET_VERSION_V2) -> MarketMetadata:
    constraints = MarketTradingConstraints(
        minimum_tick_size=Decimal("0.01"),
        minimum_order_size=Decimal("5"),
        fees_enabled=True,
        fee_rate=Decimal("0.04"),
        fee_exponent=1,
        fee_taker_only=True,
        fee_rebate_rate=Decimal("0.25"),
        seconds_delay=0,
    )
    if version == MARKET_VERSION_V2:
        outcomes = [
            MarketOutcomeId(label="UP", position_id="1001", token_id=None),
            MarketOutcomeId(label="DOWN", position_id="1002", token_id=None),
        ]
    else:
        outcomes = [
            MarketOutcomeId(label="UP", token_id="2001", position_id=None),
            MarketOutcomeId(label="DOWN", token_id="2002", position_id=None),
        ]
    return MarketMetadata(
        market_id="mkt-123",
        slug="btc-5m-up-down",
        question="Will BTC be higher in 5 minutes?",
        condition_id="0xabc",
        version=MarketVersion(version),
        state_active=True,
        state_closed=False,
        accepting_orders=True,
        enable_order_book=True,
        start_date=datetime(2026, 1, 1, tzinfo=timezone.utc),
        end_date=datetime(2026, 1, 1, 0, 10, tzinfo=timezone.utc),
        event_start_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
        outcomes=outcomes,
        outcome_prices=[Decimal("0.48"), Decimal("0.52")],
        trading=constraints,
        metrics_liquidity=Decimal("10000"),
        prices_best_bid=Decimal("0.47"),
        prices_best_ask=Decimal("0.49"),
        prices_spread=Decimal("0.02"),
    )


# --------------------------------------------------------------------------- #
# Fake database (in-memory SQLite WAL)
# --------------------------------------------------------------------------- #
@pytest.fixture
def db():
    fd, path = tempfile.mkstemp(suffix=".db")
    import os
    os.close(fd)
    database = Database(path)
    database.initialize()
    yield database
    database.close()
    try:
        import os as _os
        _os.unlink(path)
    except OSError:
        pass


# --------------------------------------------------------------------------- #
# Mock exchange adapter
# --------------------------------------------------------------------------- #
class MockAdapter(ExchangeAdapter):
    def __init__(self, mode=MODE.PAPER):
        self.mode = mode
        self.connected = True
        self.orders = []
        self.cancelled = []
        self.balance_usd = Decimal("1000")
        self.allowances = {}
        self.books = {}

    async def is_connected(self):
        return self.connected

    async def discover_markets(self, query="BTC", closed=False, end_within_minutes=None):
        return [_make_metadata(MARKET_VERSION_V2), _make_metadata(MARKET_VERSION_V1)]

    async def get_market_metadata(self, market_id):
        return _make_metadata(MARKET_VERSION_V2)

    async def get_order_book(self, asset_id):
        return self.books.get(asset_id)

    async def get_balance_usd(self):
        return self.balance_usd

    async def check_allowance(self, asset_id, version):
        return self.allowances.get((asset_id, str(version)), True)

    async def place_order(self, market_id, asset_id, version, side, price,
                          size_shares, order_type, max_spend_usd=None):
        rec = {
            "market_id": market_id, "asset_id": asset_id, "side": side.value,
            "price": str(price), "size": str(size_shares), "type": order_type.value,
            "max_spend": str(max_spend_usd) if max_spend_usd else None,
        }
        self.orders.append(rec)
        return {
            "ok": True,
            "order_id": f"ord-{len(self.orders)}",
            "status": "matched",
            "making_amount": str(size_shares * (price or Decimal("0"))),
            "taking_amount": str(size_shares),
            "trade_ids": [],
            "transactions_hashes": [],
            "simulated": self.mode == MODE.PAPER,
        }

    async def cancel_order(self, order_id):
        self.cancelled.append(order_id)
        return True

    async def cancel_all_orders(self):
        n = len(self.orders)
        self.orders.clear()
        return n

    async def get_position(self, asset_id):
        return None

    async def get_open_orders(self):
        return [{"order_id": o["order_id"], "asset_id": o["asset_id"],
                 "side": o["side"], "status": "live"} for o in self.orders]

    async def settle_and_redeem(self, market_id):
        return {"market_id": market_id, "redeemable": False,
                "note": "Redemption must be triggered explicitly via position lifecycle."}

    async def server_time(self):
        import time
        return time.time()

    async def start(self):
        self.connected = True

    async def close(self):
        self.connected = False


@pytest.fixture
def adapter():
    return MockAdapter(mode=MODE.PAPER)


# --------------------------------------------------------------------------- #
# Settings fixture
# --------------------------------------------------------------------------- #
@pytest.fixture
def settings():
    return Settings(mode=MODE.PAPER)


# --------------------------------------------------------------------------- #
# Event loop fixture for async tests
# --------------------------------------------------------------------------- #
@pytest.fixture
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()
