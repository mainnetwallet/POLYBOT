# POLYBOT Configuration Guide

Configuration comes from two sources:

1. `.env` — environment variables (secrets, runtime toggles).
2. `config/settings.yaml` — structured trading parameters.

`load_settings()` merges them, with `.env` taking precedence for values it
defines. Safe PAPER defaults are used for everything else. Capital remains
unset until you configure `POLYBOT_CAPITAL_USD`.

## .env reference

| Variable | Purpose | Default |
|---|---|---|
| POLYBOT_MODE | Operating mode: `PAPER` or `LIVE` | PAPER |
| POLYBOT_TARGET_MODE | Target mode | LIVE |
| POLYMARKET_WALLET_ADDRESS | Your Polygon wallet address | (unset) |
| POLYMARKET_PRIVATE_KEY | Signer private key (LIVE only) | (unset) |
| POLYMARKET_CLOB_API_KEY / SECRET / PASSPHRASE | CLOB L2 API credentials | (unset) |
| POLYBOT_CAPITAL_USD | Allocated pUSD capital | (unset — required to trade) |
| POLYBOT_RISK_PER_TRADE | Max planned loss per trade | 0.02 (2%) |
| POLYBOT_DAILY_LOSS_LIMIT | Max daily loss | 0.04 (4%) |
| POLYBOT_MAX_CONSECUTIVE_LOSSES | Max consecutive losses | 2 |
| POLYBOT_MIN_PROBABILITY | Min entry probability | 0.60 |
| POLYBOT_MIN_NET_EDGE | Min net edge | 0.05 |
| POLYBOT_MAX_SPREAD | Max spread ($) | 0.03 |
| POLYBOT_MAX_SLIPPAGE | Max expected slippage ($) | 0.01 |
| POLYBOT_ENTRY_WINDOW_SECONDS | Entry window | 120 |
| POLYBOT_TAKE_PROFIT_ROI | Take-profit ROI | 0.20 |
| POLYBOT_STOP_LOSS_ROI | Stop-loss ROI | 0.25 |
| POLYBOT_TAKER_DELAY_MS | Simulated taker delay | 250 |
| POLYBOT_DASHBOARD_HOST / PORT | Dashboard bind | 127.0.0.1:8000 |
| POLYBOT_DASHBOARD_ADMIN_USER / PASS | Dashboard login | admin / changeme |
| POLYBOT_HEARTBEAT_ENABLED | Heartbeat (never in dev) | false |
| POLYBOT_LOG_LEVEL | Log level | INFO |

## settings.yaml reference

Mirrors the .env values under `capital`, `risk`, `entry`, `exit`,
`fees`, `feeds`, `geo`, `dashboard`, `live_gate`, `heartbeat`.

## Key rules enforced in config

- **Capital must be configured** before any trade; there is no invented
  starting balance.
- **Risk per trade max 2%**, daily loss max 4%.
- **One active position, one entry per market.**
- **Failover = BLOCK:** a failing primary feed stops new entries rather
  than silently switching providers.
- **Feature levels:** risk protections ACTIVE, experimental strategies
  SHADOW, correlation inputs SHADOW, auto feed failover BLOCK.
- **Tick size & minimum order size are read from market metadata**, never
  hardcoded.

## How to change a value

Edit `.env` or `config/settings.yaml`, then restart POLYBOT
(`stop.bat` then `.\start.bat`). Changes take effect on next start.
