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
├── update.bat         # Windows: pull latest commits + refresh deps/DB
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

## Getting started

### 1. Requirements

- Python **3.11+** (3.11.9 tested)
- Git
- Internet access (dependencies + Polymarket APIs)
- PAPER mode needs no funds. LIVE needs a Polymarket wallet with pUSD.

### 2. Get the code

```
git clone https://github.com/mainnetwallet/POLYBOT.git
cd POLYBOT
```

(The repo is private: use your GitHub login or a personal access token when
Git asks. Never put the token inside a file in this repo.)

### 3. Install

**Windows:** run `setup.bat`. It creates `venv\`, installs
`requirements.txt`, creates `.env` and `config\settings.yaml` from the
examples, creates `data\` and `logs\`, and initializes the database.

**Linux / macOS:**

```
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
cp config/settings.yaml.example config/settings.yaml
mkdir -p data logs
python -c "from polybot.database import Database; Database('data/polybot.db').initialize()"
```

### 4. Environment setup (`.env`)

Open `.env` and fill in at least:

```ini
POLYBOT_MODE=PAPER
POLYMARKET_WALLET_ADDRESS=0xYourWalletAddress
POLYBOT_CAPITAL_USD=1000
POLYBOT_DASHBOARD_ADMIN_USER=admin
POLYBOT_DASHBOARD_ADMIN_PASSWORD=choose-a-strong-password
```

- `POLYBOT_CAPITAL_USD` must be set, otherwise the bot will not trade.
- If `POLYBOT_DASHBOARD_ADMIN_PASSWORD` is empty the dashboard falls back to
  the weak default `changeme`. Always set your own.
- LIVE-only values (leave empty for PAPER): `POLYMARKET_PRIVATE_KEY`,
  `POLYMARKET_CLOB_API_KEY`, `POLYMARKET_CLOB_API_SECRET`,
  `POLYMARKET_CLOB_API_PASSPHRASE`. Create CLOB keys at polymarket.com ->
  Settings -> API Keys. Use a dedicated trading wallet.
- `.env` is git-ignored. Never commit it or share it.
- Full variable list: `CONFIGURATION.md`. Trading parameters live in
  `config/settings.yaml`.

### 5. Run

**Windows:** `start.bat`

**Linux / macOS:**

```
source venv/bin/activate
python -m polybot
```

POLYBOT starts in **PAPER** mode. Open `http://127.0.0.1:8000`, sign in with
the admin user/password from `.env`, and watch PAPER behavior. Stop with
`Ctrl+C` or `stop.bat`.

LIVE stays locked until you type `LIVE` in the dashboard and the 23-condition
LIVE gate passes (see "Live gate" below).

### 6. Updating when new commits arrive

Stop POLYBOT first (`stop.bat` or `Ctrl+C`).

**Windows:** run `update.bat`. It does the steps below for you.

**Manual (any OS):**

```
git pull --ff-only
venv\Scripts\pip install -r requirements.txt      # Windows
# source venv/bin/activate && pip install -r requirements.txt   # Linux/macOS
python -c "from polybot.database import Database; Database('data/polybot.db').initialize()"
```

Then start again (`start.bat` / `python -m polybot`).

After every update:

- Your `.env`, `config/settings.yaml`, `data/` and `logs/` are git-ignored, so
  `git pull` never overwrites them.
- New settings may have been added. Compare your files with the examples and
  copy over any new keys:
  - `git diff HEAD@{1} HEAD -- .env.example config/settings.yaml.example`
- Read the new commits: `git log --oneline -10` and the docs they changed.
- Run the tests: `python -m pytest tests/` (use `venv\Scripts\python` on
  Windows).
- Back up `data/polybot.db` before updating if you have PAPER/LIVE history.

If `git pull --ff-only` fails because of your local edits:

```
git stash
git pull --ff-only
git stash pop
```

To discard local code changes and match the remote exactly (this deletes your
uncommitted code edits, but not `.env` or `data/`):

```
git fetch origin
git reset --hard origin/master
```

### Troubleshooting

| Problem | Fix |
|---|---|
| `python` not found | Install Python 3.11+ and tick "Add to PATH", then rerun setup |
| `ModuleNotFoundError: polybot.data` | Your checkout is missing `polybot/data/`. Run `git pull` to get the latest code |
| `Virtual environment not found` | Run `setup.bat` (or the Linux/macOS install steps) first |
| Dashboard login fails | Check `POLYBOT_DASHBOARD_ADMIN_USER` / `_PASSWORD` in `.env`, restart |
| Bot does not trade | Set `POLYBOT_CAPITAL_USD`; NO TRADE is normal when no valid edge exists |
| Port 8000 in use | Change `POLYBOT_DASHBOARD_PORT` in `.env` |
| Dependency errors after update | Rerun `pip install -r requirements.txt` inside the venv |

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
