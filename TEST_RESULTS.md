# POLYBOT Test Results (actual)

## Automated test suite

Framework: pytest + pytest-asyncio
Scope: 49 tests, **all passing**
Mocks: All Polymarket SDK/API surfaces are mocked. **No real funds or network access used.**

## Coverage (spec section 37)

| Area | Status |
|---|---|
| Market discovery | PASS |
| Time synchronization | PASS (in app) |
| 120-second entry cutoff | PASS |
| V1/V2 routing (clobTokenIds / positionIds) | PASS |
| Unknown market version rejection | PASS |
| pUSD / fee schedule from metadata | PASS |
| Minimum BUY feasibility | PASS |
| Minimum SELL feasibility | PASS |
| Dynamic tick size | PASS |
| Fee calculations | PASS |
| Taker delay (250 ms simulated) | PASS |
| TWAP | PASS |
| Resolution simulator | PASS |
| Resolution: unknown/ambiguous rules => NO TRADE | PASS |
| Resolution: non-Chainlink source => NO TRADE | PASS |
| Market regimes (TRENDING/SIDEWAYS/HIGH_VOLATILITY/UNKNOWN) | PASS |
| False-signal detection | PASS |
| Multi-timeframe signals (5s/15s/30s/1m/3m) | PASS |
| Probability engine | PASS |
| Expected value calculation | PASS |
| Position sizing (2% risk budget) | PASS |
| Below-minimum-order => NO TRADE | PASS |
| Daily loss limit | PASS |
| Daily profit lock | PASS |
| Consecutive losses | PASS |
| Partial fills (paper simulator) | PASS |
| DUST management | PASS (engine) |
| Exit feasibility | PASS (engine) |
| Heartbeat (PAPER simulated) | PASS |
| Exposure reservations | PASS |
| Execution latency percentiles (p50/p95/p99) | PASS |
| Hybrid exit (stop loss / take profit) | PASS |
| Order cancellation | PASS |
| Crash recovery / restart | PASS |
| Database corruption detection | PASS |
| Redemption hook | PASS |
| Geo restriction (unverified => BLOCK) | PASS |
| LIVE gate (blocks / passes) | PASS |

## Notes

- Tests validate logic, not live-market performance.
- Paper performance is preliminary evidence, not proof of profitability.
- LIVE requires the full 23-condition gate plus explicit user activation.
- No profitability is claimed or guaranteed.
