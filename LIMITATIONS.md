# POLYBOT Known Limitations

## General

- POLYBOT does **not** guarantee profits. Prediction-market trading
  involves risk of loss.
- LIVE trading is locked until the user explicitly activates it and the
  full 23-condition LIVE gate passes.
- No starting balance is invented; capital must be configured
  (`POLYBOT_CAPITAL_USD`).
- BTC 5-minute UP/DOWN markets may not always be listed; POLYBOT remains
  NO TRADE when no qualifying market is discovered.

## Market data & feeds

- Primary/secondary BTC feeds and Chainlink TWAP come from PolyBolt
  (`wss://ws-live-v2.polymarket.com/ws`), which requires CLOB API
  credentials for price channels. Without credentials, the feed starts
  UNVERIFIED and new entries are BLOCKED.
- A secondary independent on-chain Chainlink feed is not separately wired;
  redundancy is provided by a redundant PolyBolt connection plus
  failover=BLOCK.
- The 30-second TWAP has no PolyBolt replacement; only the 60-second
  Chainlink TWAP is supported natively.
- Correlation inputs (ETH, broad market) default to SHADOW and never
  independently create an entry.

## Exchange / SDK

- Integrates `polymarket-client==0.16.0`. Some SDK methods referenced
  (cancel_order, cancel_all_orders, list_orders, list_positions) depend
  on the installed SDK version exposing them; graceful degradation is
  implemented where a method is absent.
- Direct CLOB REST (L2 HMAC signing) is provided as a verification path
  in `clob_rest.py`.
- Wallet approvals (pUSD, outcome tokens) must be set up out-of-band;
  POLYBOT never approves unlimited allowances.

## Heartbeat

- Never activated during development. Production heartbeat requires LIVE
  conditions and a dedicated API key.
- Heartbeat cancellation may affect ALL open orders across the API key.

## Resolution

- The resolution simulator supports snapshot, 60s TWAP and chainlink TWAP
  methodologies. Unknown/ambiguous rules => NO TRADE.
- LIVE replay validation requires ≥20 resolved markets with zero
  mismatches; if data is insufficient, POLYBOT remains PAPER.

## Dashboard

- Bound to 127.0.0.1 only. Not a public-facing server.
- Default admin password `changeme` must be changed in `.env`.

## Windows

- Single-instance lock uses `msvcrt.file_locking` (Windows); best-effort
  on other platforms.

## Testing

- Automated tests use mocks; they validate logic, not live-market
  performance.
- Historical backtesting and PAPER validation reports are produced where
  data permits; they are not profitability guarantees.
