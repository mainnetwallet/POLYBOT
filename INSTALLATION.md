# POLYBOT Installation Guide

## Prerequisites

- Windows 10/11
- Python 3.11+ (3.11.9 tested)
- Internet access (for dependency install and Polymarket API access)
- A Polymarket account (International platform) with a wallet and pUSD
  (for LIVE; PAPER works without funds)

## Step 1 — Get the code

```powershell
git clone https://github.com/mainnetwallet/POLYBOT.git
cd POLYBOT
```

Later, to get new commits, see "Updating" in `README.md` (or run
`update.bat`).

## Step 2 — Run setup

```powershell
cd POLYBOT
.\setup.bat
```

This:
1. Creates `venv\` (Python virtual environment).
2. Installs pinned dependencies from `requirements.txt`, including
   `polymarket-client==0.16.0` (the official unified Python SDK).
3. Copies `.env.example` → `.env` (if absent).
4. Copies `config/settings.yaml.example` → `config/settings.yaml` (if absent).
5. Creates `data\` and `logs\`.
6. Initializes the SQLite WAL database at `data/polybot.db`.

## Step 3 — Configure

Edit `.env` (never commit it). Minimum for a useful run:

```ini
POLYBOT_MODE=PAPER
POLYMARKET_WALLET_ADDRESS=0xYourWalletAddress
POLYBOT_CAPITAL_USD=1000
POLYBOT_DASHBOARD_ADMIN_PASSWORD=your-strong-password
```

For LIVE (only after extensive PAPER validation), also set:

```ini
POLYMARKET_PRIVATE_KEY=...
POLYMARKET_CLOB_API_KEY=...
POLYMARKET_CLOB_API_SECRET=...
POLYMARKET_CLOB_API_PASSPHRASE=...
```

Edit `config/settings.yaml` as needed (risk limits, entry parameters,
dashboard bind address). Dashboard is hard-bound to `127.0.0.1`.

## Step 4 — Launch

```powershell
cd POLYBOT
.\start.bat
```

POLYBOT starts in **PAPER** mode. Open `http://127.0.0.1:8000` and sign
in with the admin password.

## Step 5 — Validate before LIVE

- Run PAPER and review the dashboard, logs and test results.
- Run the automated suite (after `cd POLYBOT`): `venv\Scripts\python -m pytest tests/`
- Review `TEST_RESULTS.md` and `LIMITATIONS.md`.
- Only when the LIVE gate passes and you have typed `LIVE` to confirm
  should you consider activating LIVE.

## Shutdown

```powershell
cd POLYBOT
.\stop.bat
```

Or press Ctrl+C in the `start.bat` window.

## Uninstall

Delete the `POLYBOT` folder, `venv\`, `data\`, `logs\`, and `.env`.
There are no system-wide components.
