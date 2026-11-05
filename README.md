# POLYBOT — Autonomous Polymarket BTC 5-Minute UP/DOWN Trading Agent

Production build specification implementation. POLYBOT is a complete,
local-first autonomous trading application for Polymarket BTC 5-minute
UP/DOWN markets. It combines quantitative prediction, Chainlink TWAP
analysis, adaptive market intelligence, false-signal detection, dynamic
entry, profit optimization, advanced execution, hybrid exit, SELL
feasibility, capital protection, strategy intelligence, portfolio
exposure, heartbeat safety, disaster recovery and a secure local
dashboard.

## ⚠️ Important disclaimers

- **No guaranteed profits.** Trading prediction markets involves risk of
  loss. POLYBOT never promises profitability.
- **NO TRADE is better than a bad trade.** The bot only trades when a
  genuine, validated positive expected-value opportunity exists; it
  remains NO TRADE otherwise.
- **LIVE trading is locked by default.** POLYBOT starts in **PAPER**
  mode. Real-money execution is disabled until (a) you explicitly
  activate LIVE in the dashboard, (b) the strict 23-condition LIVE gate
  passes, and (c) you type `LIVE` to confirm. No real-money order is
  ever sent automatically.
- **Do not use real funds until you have reviewed PAPER results and the
  LIVE readiness report.**

## Project status

Complete, implementable application. All phases of the build
specification are implemented:

1. Project foundation (config, env, DB, migrations, logging, adapters)
2. Market & data engine (discovery, BTC feeds, Chainlink TWAP, lifecycle)
3. Trading intelligence (probability, regimes, MTF, false signals)
4. Profit & execution (fees, EV, sizing, min-order, order execution)
5. Exit & risk (hybrid exits, DUST, exposure ledger, daily protections)
6. Heartbeat & recovery (heartbeat service, order journal, crash recovery)
7. Analytics & dashboard (secure local web dashboard)
8. Testing & validation (automated test suite)
9. Final integration (Windows scripts, docs)

## Repository layout

```
POLYBOT/
├── setup.bat          # Windows: venv, deps, config, database
├── start.bat          # Windows: launch POLYBOT (PAPER by default)
├── stop.bat           # Windows: shutdown POLYBOT
├── pyproject.toml
├── requirements.txt   # pinned dependencies (polymarket-client==0.16.0)
├── .env.example       # copy to .env and configure (never commit .env)
├── config/settings.yaml.example
├── README.md
├── SECURITY.md
├── INSTALLATION.md
├── CONFIGURATION.md
├── OPERATIONS.md
├── LIMITATIONS.md
├── TEST_RESULTS.md
├── polybot/           # application package
│   ├── __main__.py
│   ├── main.py        # entry point (orchestrator + dashboard)
│   ├── app.py         # trading orchestrator
│   ├── config.py      # settings loader
│   ├── constants.py   # contracts, domains, enums
│   ├── types.py       # shared types
│   ├── decimal_math.py
│   ├── clock.py       # server-time sync
│   ├── idempotency.py
│   ├── database.py    # SQLite WAL trading state
│   ├── adapters/      # Polymarket SDK / CLOB REST / PolyBolt / market stream
│   ├── data/          # feeds, Parquet/DuckDB analytics store
│   ├── engine/        # discovery, lifecycle, prediction, regime, signals,
│   │                  # entry, profit, sizing, risk, exit, resolution, heartbeat
│   ├── execution/     # order manager (journal), paper simulator
│   ├── recovery/      # order journal, crash recovery, reconciliation
│   ├── validation/    # strict LIVE gate, geo restriction
│   └── dashboard/     # FastAPI dashboard (auth, CSRF, LIVE confirmation)
└── tests/             # automated test suite (mocked APIs, no real funds)
```

## Quick start (Windows)

1. `setup.bat` — creates the venv, installs pinned dependencies, copies
   `.env.example` → `.env` and `config/settings.yaml.example`, and
   initializes the SQLite WAL database.
2. Edit `.env` — set `POLYMARKET_WALLET_ADDRESS` and
   `POLYBOT_CAPITAL_USD` (capital remains unset until you configure it).
3. `start.bat` — launches POLYBOT in **PAPER** mode. The dashboard is at
   `http://127.0.0.1:8000`.
4. Sign in with the dashboard admin password (set
   `POLYBOT_DASHBOARD_ADMIN_PASSWORD` in `.env`).
5. Review PAPER behavior. When ready and the LIVE gate passes, type
   `LIVE` in the dashboard and activate LIVE.
6. `stop.bat` — shuts POLYBOT down.

## Security

- Dashboard binds to **127.0.0.1 only**.
- Authentication + CSRF protection.
- Typed `LIVE` confirmation required to activate LIVE.
- No secrets are stored in the dashboard or source code.
- Keep production credentials (private key, CLOB API secret) in `.env`,
  outside source control.
- Use a dedicated trading wallet and a dedicated CLOB API key for POLYBOT.
- Heartbeat cancellation may affect ALL open orders across the API key;
  heartbeat is never activated during development.

See `SECURITY.md` for the full security policy.

## Defaults (safe, PAPER)

| Setting | Value |
|---|---|
| Mode | PAPER |
| Platform | International Polymarket |
| Market | BTC 5M UP/DOWN |
| Collateral | pUSD |
| Risk per trade | 2% |
| Daily loss limit | 4% |
| Max consecutive losses | 2 |
| Max active positions | 1 |
| Max entries per market | 1 |
| Entry window | 120 seconds |
| Min probability | 60% |
| Min net edge | 5 percentage points |
| Max spread | $0.03 |
| Max slippage | $0.01 |
| Take profit | +20% ROI |
| Stop loss | -25% ROI |
| Taker delay (simulated) | 250 ms |
| Failover | BLOCK (never silently switch feeds) |
| Heartbeat | disabled |

Capital is **unset until you configure it** (`POLYBOT_CAPITAL_USD`).

## Live gate

LIVE requires all 23 conditions (explicit activation, geo eligibility,
wallet auth, signature/funder verification, pUSD balance, correct
allowances, clean reconciliation, READY recovery, healthy feeds,
verified resolution rules, user-approved fingerprints, resolution replay
validation (≥20 markets, zero mismatches), current metadata, minimum BUY
& SELL feasibility, risk checks, exposure monitor ACTIVE, disaster
recovery ACTIVE, heartbeat safely configured, no shutdown, correct
market version, statistical validation, final LIVE confirmation). Any
failure leaves LIVE disabled with an exact reason.

## Testing

Tests use mocked APIs only — never real funds. Run:

```
venv\Scripts\python -m pytest tests/
```

See `TEST_RESULTS.md` for actual results.

## License

MIT. See `LICENSE` if present.
