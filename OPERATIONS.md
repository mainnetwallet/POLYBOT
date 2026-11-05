# POLYBOT Operations Guide

## Normal operation

1. `start.bat` → POLYBOT launches in PAPER mode.
2. Open `http://127.0.0.1:8000`, sign in.
3. Monitor the dashboard: Account, Market, Strategy, Position, System,
   Controls panels.
4. PAPER trading simulates queue position, partial fills, slippage, fees
   and the 250 ms taker delay. It does NOT use real funds.

## Activating LIVE (read SECURITY.md first)

1. Ensure PAPER validation is complete and you accept the risks.
2. In the dashboard, type `LIVE` in the confirmation box and click
   Confirm LIVE.
3. The strict LIVE gate (23 conditions) evaluates. If all pass, LIVE is
   activated and real-money orders may be sent.
4. If any condition fails, LIVE remains disabled with an exact reason.

LIVE is never activated automatically.

## Emergency stop

- Dashboard → Emergency Stop: requests shutdown and cancels all open
  orders via the adapter.
- `stop.bat`: terminates the POLYBOT process.
- On restart, the recovery sequence runs (journal restore, risk restore,
  clock sync, feed/CLOB reconnect, reconciliation) before entering READY.

## Recovery

On startup POLYBOT:
1. Acquires a single-instance lock.
2. Verifies database integrity (`PRAGMA quick_check`).
3. Restores persistent state, risk protections, exposure reservations.
4. Verifies clock (syncs with Polymarket server time).
5. Reconnects feeds and CLOB.
6. Queries balances, open orders, recent trades.
7. Reconciles positions and resolves unknown order intents (never blindly
   retried).
8. Re-runs LIVE gates.
9. Enters READY only after successful reconciliation.

No new entries during recovery.

## Logs & data

- `logs/` — application logs (configure `POLYBOT_LOG_LEVEL`).
- `data/polybot.db` — SQLite WAL trading state (orders, positions, risk,
  heartbeats, audit log).
- `data/analytics/` — Parquet market history and signals.

## Maintenance

- Periodically back up `data/polybot.db` and `data/analytics/`.
- Rotate logs as needed.
- Re-run `setup.bat` to refresh dependencies after dependency changes.
