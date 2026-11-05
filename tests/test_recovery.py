"""Recovery, restart, database corruption, cancellation, redemption tests."""
from __future__ import annotations

import asyncio
from decimal import Decimal
import pytest

from polybot.clock import ServerClock
from polybot.database import Database
from polybot.recovery.journal import RecoveryManager, RecoveryState
from polybot.constants import MODE
from polybot.types import TradeSide, OrderType, OrderIntentStatus
from polybot.execution.order_manager import OrderManager
from tests.conftest import MockAdapter


def _register_market(db, market_id="mkt-1"):
    db.execute(
        "INSERT INTO markets(market_id, version, state, active, closed, accepting_orders) "
        "VALUES (?,?,?,?,?,?)",
        (market_id, "v2", "ACTIVE", 1, 0, 1),
    )


class FakeApp:
    def __init__(self, db, mode=MODE.PAPER):
        self.db = db
        self.mode = mode
        self.clock = ServerClock()
        self.adapter = MockAdapter(mode=mode)
        self.risk = type("R", (), {"restore": lambda self: None})()
        self.feeds = type("F", (), {
            "start": lambda self: asyncio.sleep(0),
            "stop": lambda self: None,
        })()
        self.market_stream = type("M", (), {
            "connect": lambda self: asyncio.sleep(0),
            "disconnect": lambda self: None,
        })()
        self.order_manager = None
        self.heartbeat = None
        self.shutdown_requested = False
        self.lifecycle = type("L", (), {
            "restore_from_db": lambda self: None,
        })()
        from polybot.execution.order_manager import OrderManager
        self.order_manager = OrderManager(db, self.adapter)
        async def _fake_eval(self=None):
            return type("GR", (), {"passed": True})()
        self.live_gate = type("G", (), {"evaluate": _fake_eval})()
        self._validation_evidence = True
        self.adapter_balance = self.adapter.get_balance_usd
        self.adapter_open_orders = self.adapter.get_open_orders


@pytest.mark.asyncio
async def test_recovery_stages_ok(db):
    app = FakeApp(db)
    rec = RecoveryManager(db, app)
    state = await rec.recover()
    assert state.ok is True
    assert state.ready is True


@pytest.mark.asyncio
async def test_recovery_db_corruption_blocks(tmp_path):
    db_path = tmp_path / "corrupt.db"
    db_path.write_bytes(b"not a sqlite database")
    db = Database(str(db_path))
    try:
        try:
            db.initialize()
        except Exception:
            pass
        ok = db.integrity_check()
    finally:
        db.close()
    assert ok is False


@pytest.mark.asyncio
async def test_cancellation_verification():
    adapter = MockAdapter(mode=MODE.PAPER)
    ok = await adapter.cancel_order("ord-999")
    assert ok is True
    assert "ord-999" in adapter.cancelled


@pytest.mark.asyncio
async def test_cancel_all_orders():
    adapter = MockAdapter(mode=MODE.PAPER)
    n = await adapter.cancel_all_orders()
    assert isinstance(n, int)


@pytest.mark.asyncio
async def test_restart_restores_journal(db):
    adapter = MockAdapter(mode=MODE.PAPER)
    _register_market(db)
    om = OrderManager(db, adapter)
    await om.journal_intent(
        market_id="mkt-1", condition_id=None, asset_id="1001",
        side=TradeSide.BUY, price=Decimal("0.5"), size_shares=Decimal("10"),
        order_type=OrderType.FAK,
    )
    om2 = OrderManager(db, adapter)
    await om2.load_from_db()
    assert len(om2.all_entries()) == 1


@pytest.mark.asyncio
async def test_redemption_hook():
    adapter = MockAdapter(mode=MODE.PAPER)
    res = await adapter.settle_and_redeem("mkt-1")
    assert res["redeemable"] is False
    assert "Redemption" in res.get("note", "")
