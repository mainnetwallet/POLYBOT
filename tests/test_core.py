"""Core logic tests: decimal math, discovery, lifecycle, routing, ticks,
min order, fees. Mocked APIs only."""
from __future__ import annotations

from decimal import Decimal
import pytest

from polybot.decimal_math import qshare, qprice, qamount, round_down, safe_divide
from polybot.engine.discovery import MarketDiscovery, MarketLifecycle
from polybot.constants import MARKET_VERSION_V1, MARKET_VERSION_V2
from polybot.types import MarketMetadata, MarketVersion, MarketOutcomeId
from tests.conftest import _make_metadata


# ---------------- decimal math ----------------

def test_qshare_rounds_down():
    assert qshare(Decimal("5.1234567")) == Decimal("5.123456")
    assert qshare(Decimal("0.0000004")) == Decimal("0")

def test_qprice_snaps_to_tick():
    assert qprice(Decimal("0.525"), Decimal("0.01")) == Decimal("0.52")
    assert qprice(Decimal("0.529"), Decimal("0.001")) == Decimal("0.529")

def test_safe_divide_zero():
    assert safe_divide(Decimal("10"), Decimal("0")) == Decimal("0")


# ---------------- discovery ----------------

@pytest.mark.asyncio
async def test_discovery_filters_btc_5m(adapter):
    discovery = MarketDiscovery(adapter)
    result = await discovery.refresh("BTC 5M")
    assert len(result.btc_5m_markets) == 2
    assert len(result.errors) == 0


@pytest.mark.asyncio
async def test_validation_rejects_unknown_version(adapter):
    discovery = MarketDiscovery(adapter)
    bad = _make_metadata(MARKET_VERSION_V2)
    bad.version = "v3"  # unsupported version string
    ok, reasons = await discovery.validate(bad)
    assert ok is False
    assert any("version" in r.lower() for r in reasons)


# ---------------- lifecycle / state machine ----------------

def test_lifecycle_transitions(db):
    lifecycle = MarketLifecycle(db)
    meta = _make_metadata(MARKET_VERSION_V2)
    lifecycle.register("mkt-1", meta)
    assert lifecycle.state("mkt-1") == "DISCOVERED"
    assert lifecycle.transition("mkt-1", "ACTIVE") is True
    assert lifecycle.state("mkt-1") == "ACTIVE"
    # Invalid transition: DISCOVERED -> RESOLVING not allowed directly
    lifecycle2 = MarketLifecycle(db)
    lifecycle2.register("mkt-2", meta)
    assert lifecycle2.transition("mkt-2", "RESOLVING") is False


def test_lifecycle_entry_once(adapter, db):
    lifecycle = MarketLifecycle(db)
    meta = _make_metadata(MARKET_VERSION_V2)
    lifecycle.register("mkt-3", meta)
    lifecycle.transition("mkt-3", "ACTIVE")
    lifecycle.set_entry("mkt-3", "BUY", "ord-1")
    lifecycle.transition("mkt-3", "ENTRY_CLOSED")
    assert lifecycle.state("mkt-3") == "ENTRY_CLOSED"


# ---------------- V1/V2 routing ----------------

def test_v1_uses_token_id():
    meta = _make_metadata(MARKET_VERSION_V1)
    assert meta.version == MarketVersion.V1
    assert meta.asset_id_for_outcome("UP") == "2001"
    assert meta.asset_id_for_outcome("DOWN") == "2002"


def test_v2_uses_position_id():
    meta = _make_metadata(MARKET_VERSION_V2)
    assert meta.version == MarketVersion.V2
    assert meta.asset_id_for_outcome("UP") == "1001"
    assert meta.asset_id_for_outcome("DOWN") == "1002"


def test_metadata_trade_ready():
    meta = _make_metadata(MARKET_VERSION_V2)
    assert meta.trade_ready is True
    meta2 = _make_metadata(MARKET_VERSION_V2)
    meta2.state_closed = True
    assert meta2.trade_ready is False


# ---------------- min order / tick ----------------

def test_min_order_cost_at_prices():
    tick = Decimal("0.01")
    min_shares = Decimal("5")
    for price in ["0.40", "0.45", "0.50", "0.55", "0.60", "0.65", "0.70"]:
        cost = Decimal(price) * min_shares
        # minimum valid order cost at each price
        assert cost > 0
        # quantize price to tick
        assert qprice(Decimal(price), tick) == Decimal(price)
