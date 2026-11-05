# POLYBOT Security Policy

## Threat model & assumptions

- POLYBOT runs on your local Windows machine, not a public server.
- The dashboard is **bound to 127.0.0.1 only** and is not exposed to the
  internet.
- You are responsible for the physical and OS security of the host.

## Credentials & secrets

- Never commit `.env`, `config/settings.yaml` (with real values), or any
  file containing private keys, API secrets or passphrases.
- `POLYMARKET_PRIVATE_KEY` — signer private key. Required for LIVE only.
- `POLYMARKET_CLOB_API_SECRET` — base64-encoded L2 HMAC secret. Treat as
  a secret.
- Use a **dedicated trading wallet** and a **dedicated CLOB API key**
  exclusively for POLYBOT. Heartbeat cancellation may affect ALL open
  orders associated with the API key across markets and processes.

## Wallet / approvals

- POLYBOT never approves unlimited allowances. Allowances must be set up
  out-of-band (e.g. `setup_trading_approvals()`) with exact amounts.
- Approved contracts: pUSD, Standard Exchange, Polymarket V2 Exchange,
  Neg Risk Exchange, Neg Risk Adapter, Conditional Tokens, Position
  Manager (see `polybot/constants.py`).
- Redemption (split/merge/redeem) must be triggered explicitly; POLYBOT
  does not auto-redeem.

## Geographic restrictions

- POLYBOT checks the official geographic restriction endpoint at startup,
  before LIVE activation, and periodically.
- Results default to **UNVERIFIED / BLOCK** when the endpoint is
  unreachable.
- Never bypass geo restrictions using VPNs or proxies.
- A prior "unblocked" observation is not a permanent eligibility
  guarantee.

## Heartbeat safety

- Never send a production heartbeat during development.
- Never activate heartbeat until LIVE conditions pass.
- Heartbeat does NOT close filled positions.
- Treat heartbeat-triggered cancellation as a normal recovery event.

## Dashboard security

- Authentication required (session cookie, httpOnly, same-site lax).
- CSRF protection via `X-CSRF-Token` header.
- Typed `LIVE` confirmation required to activate real-money trading.
- No secrets are rendered by the dashboard; it shows backend state only.
- Admin password: set `POLYBOT_DASHBOARD_ADMIN_PASSWORD` in `.env`
  (default `changeme` — change it before any use).

## Order handling

- Every order is journaled (idempotency key, market, asset, side, price,
  size, strategy version, timestamp) **before** submission.
- Unknown orders are never blindly retried.
- Cancellation is never assumed without verification.
- No Martingale, averaging down, or automatic risk increases.

## Disclosure

If you discover a security issue, do not open a public issue. Contact
the maintainer privately.
