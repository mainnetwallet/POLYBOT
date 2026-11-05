"""POLYBOT secure local dashboard.

FastAPI application bound to 127.0.0.1 only. Authentication required, CSRF
protection, typed LIVE confirmation, no secrets exposed, actual backend state
only.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import os
import secrets
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request, Response, HTTPException, Depends, Form, Cookie
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from polybot.app import App
from polybot.constants import MODE
from polybot.decimal_math import dec

SESSION_TTL_SECONDS = 8 * 60 * 60


def _hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 100_000)
    return base64.b64encode(salt + dk).decode("ascii")


def _verify_password(password: str, stored: str) -> bool:
    try:
        raw = base64.b64decode(stored)
        salt, dk = raw[:16], raw[16:]
        expected = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 100_000)
        return hashlib.sha256(expected).digest() == hashlib.sha256(dk).digest()
    except Exception:
        return False


def create_app(app: App) -> FastAPI:
    app_fastapi = FastAPI(title="POLYBOT — Local Trading Dashboard")

    settings = app.settings
    admin_password = settings.dashboard.admin_password or "changeme"
    password_hash = _hash_password(admin_password)

    # Sessions: token -> {created_at, csrf, admin}
    sessions: dict[str, dict] = {}

    templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "templates"))

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #
    def _now() -> float:
        return time.time()

    def _session_valid(token: Optional[str]) -> bool:
        if not token:
            return False
        s = sessions.get(token)
        if not s:
            return False
        return _now() - s["created_at"] < SESSION_TTL_SECONDS

    def _require_session(request: Request) -> str:
        token = request.cookies.get("session")
        if not _session_valid(token):
            raise HTTPException(status_code=401, detail="authentication required")
        return token

    def _require_csrf(request: Request, token: str) -> None:
        s = sessions.get(token)
        if not s:
            raise HTTPException(status_code=401, detail="session invalid")
        csrf_header = request.headers.get("x-csrf-token")
        if not csrf_header or csrf_header != s["csrf"]:
            raise HTTPException(status_code=403, detail="CSRF token mismatch")

    def _new_session() -> str:
        token = secrets.token_urlsafe(32)
        sessions[token] = {
            "created_at": _now(),
            "csrf": secrets.token_urlsafe(24),
            "admin": True,
        }
        # Sweep expired.
        cutoff = _now() - SESSION_TTL_SECONDS
        for k in [k for k, v in sessions.items() if v["created_at"] < cutoff]:
            sessions.pop(k, None)
        return token

    # ------------------------------------------------------------------ #
    # Routes
    # ------------------------------------------------------------------ #
    @app_fastapi.exception_handler(HTTPException)
    async def _http_exception_handler(request: Request, exc: HTTPException):
        # Browser page requests without a valid session go to the login page;
        # API calls keep getting a JSON 401/403.
        if exc.status_code == 401 and not request.url.path.startswith("/api/"):
            return RedirectResponse(url="/login", status_code=302)
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)

    @app_fastapi.get("/", include_in_schema=False)
    async def root(request: Request):
        if _session_valid(request.cookies.get("session")):
            return RedirectResponse(url="/dashboard", status_code=302)
        return RedirectResponse(url="/login", status_code=302)

    @app_fastapi.get("/login", response_class=HTMLResponse)
    async def login_page(request: Request):
        token = request.cookies.get("session")
        if _session_valid(token):
            return RedirectResponse(url="/dashboard")
        return templates.TemplateResponse(request, "login.html", {"error": None})

    @app_fastapi.post("/login")
    async def login(request: Request, password: str = Form(...)):
        if not _verify_password(password, password_hash):
            return templates.TemplateResponse(
                request, "login.html", {"error": "Invalid password"}, status_code=401
            )
        token = _new_session()
        resp = RedirectResponse(url="/dashboard", status_code=302)
        resp.set_cookie(
            "session",
            token,
            httponly=True,
            secure=False,  # localhost only
            samesite="lax",
            max_age=SESSION_TTL_SECONDS,
            path="/",
        )
        return resp

    @app_fastapi.post("/logout")
    async def logout(token: str = Depends(_require_session)):
        sessions.pop(token, None)
        resp = RedirectResponse(url="/login", status_code=302)
        resp.delete_cookie("session", path="/")
        return resp

    @app_fastapi.get("/dashboard", response_class=HTMLResponse)
    async def dashboard_page(request: Request, token: str = Depends(_require_session)):
        return templates.TemplateResponse(
            request, "dashboard.html", {"csrf": sessions[token]["csrf"]}
        )

    # -- API -----------------------------------------------------------

    @app_fastapi.get("/api/session")
    async def api_session(token: str = Depends(_require_session)):
        return {"authenticated": True, "mode": app.mode, "ready": app.ready}

    @app_fastapi.get("/api/state")
    async def api_state(token: str = Depends(_require_session)):
        return JSONResponse(app.dashboard_state())

    @app_fastapi.get("/api/markets")
    async def api_markets(token: str = Depends(_require_session)):
        markets = []
        for mid, meta in app._markets.items():
            markets.append(
                {
                    "market_id": mid,
                    "slug": meta.slug,
                    "question": meta.question,
                    "version": meta.version.value,
                    "state": app.lifecycle.state(mid) if app.lifecycle else None,
                    "accepting_orders": meta.accepting_orders,
                    "tick_size": str(meta.trading.minimum_tick_size) if meta.trading.minimum_tick_size else None,
                    "min_order_size": str(meta.trading.minimum_order_size) if meta.trading.minimum_order_size else None,
                    "fees_enabled": meta.trading.fees_enabled,
                    "liquidity": str(meta.metrics_liquidity) if meta.metrics_liquidity else None,
                    "best_bid": str(meta.prices_best_bid) if meta.prices_best_bid else None,
                    "best_ask": str(meta.prices_best_ask) if meta.prices_best_ask else None,
                    "spread": str(meta.prices_spread) if meta.prices_spread else None,
                }
            )
        return {"markets": markets}

    @app_fastapi.get("/api/positions")
    async def api_positions(token: str = Depends(_require_session)):
        positions = []
        for aid, pos in app._positions.items():
            positions.append(
                {
                    "asset_id": aid,
                    "market_id": pos.get("market_id"),
                    "side": pos.get("side"),
                    "entry_price": str(pos.get("entry_price", "0")),
                    "quantity_shares": str(pos.get("quantity_shares", "0")),
                    "cost_usd": str(pos.get("cost_usd", "0")),
                    "exit_state": "OPEN",
                }
            )
        return {"positions": positions}

    @app_fastapi.get("/api/risk")
    async def api_risk(token: str = Depends(_require_session)):
        if app.risk:
            snap = app.risk.snapshot(
                dec("0"), app.settings.capital.allocated_usd or dec("0")
            )
            return {
                "available_balance_usd": str(snap.available_balance_usd),
                "allocated_capital_usd": str(snap.allocated_capital_usd),
                "reserved_capital_usd": str(snap.reserved_capital_usd),
                "exposure_usd": str(snap.exposure_usd),
                "daily_pnl_usd": str(snap.daily_pnl_usd),
                "daily_loss_usd": str(snap.daily_loss_usd),
                "daily_loss_limit_remaining_usd": str(snap.daily_loss_limit_remaining_usd),
                "risk_budget_remaining_usd": str(snap.risk_budget_remaining_usd),
                "open_positions": snap.open_positions,
                "pending_orders": snap.pending_orders,
                "within_limits": snap.within_limits,
                "blocking_reason": snap.blocking_reason,
            }
        return {}

    @app_fastapi.get("/api/livegate")
    async def api_livegate(token: str = Depends(_require_session)):
        if app.live_gate:
            result = await app.live_gate.evaluate()
            return {
                "passed": result.passed,
                "live_enabled": result.live_enabled,
                "reason": result.reason,
                "conditions": [
                    {"name": c.name, "passed": c.passed, "reason": c.reason}
                    for c in result.conditions
                ],
            }
        return {"passed": False, "live_enabled": False, "reason": "gate not initialized"}

    @app_fastapi.post("/api/live/confirm")
    async def api_live_confirm(
        request: Request,
        confirmation: str = Form(...),
        token: str = Depends(_require_session),
    ):
        _require_csrf(request, token)
        # Typed LIVE confirmation: user must type "LIVE" exactly.
        if confirmation != "LIVE":
            raise HTTPException(status_code=400, detail='must type "LIVE" to confirm')
        if app.live_gate:
            app.live_gate.set_user_activation(True)
        return {"confirmed": True}

    @app_fastapi.post("/api/live/activate")
    async def api_live_activate(
        request: Request,
        token: str = Depends(_require_session),
    ):
        _require_csrf(request, token)
        if not app.live_gate:
            raise HTTPException(status_code=500, detail="live gate not initialized")
        result = await app.live_gate.evaluate()
        if not result.passed:
            return {"activated": False, "reason": result.reason, "conditions": [
                {"name": c.name, "passed": c.passed, "reason": c.reason} for c in result.conditions
            ]}
        app.mode = MODE.LIVE
        if app.settings:
            app.settings.mode = MODE.LIVE
        return {"activated": True, "mode": app.mode}

    @app_fastapi.post("/api/stop")
    async def api_stop(request: Request, token: str = Depends(_require_session)):
        _require_csrf(request, token)
        app.shutdown_requested = True
        if app.heartbeat:
            asyncio.create_task(app.heartbeat.stop())
        return {"stopped": True}

    @app_fastapi.post("/api/emergency-stop")
    async def api_emergency_stop(request: Request, token: str = Depends(_require_session)):
        _require_csrf(request, token)
        app.shutdown_requested = True
        if app.adapter:
            asyncio.create_task(app.adapter.cancel_all_orders())
        if app.heartbeat:
            asyncio.create_task(app.heartbeat.stop())
        return {"emergency_stopped": True}

    @app_fastapi.get("/api/heartbeat")
    async def api_heartbeat(token: str = Depends(_require_session)):
        if app.heartbeat:
            return app.heartbeat.status()
        return {}

    # Static
    static_dir = os.path.join(os.path.dirname(__file__), "static")
    if os.path.isdir(static_dir):
        app_fastapi.mount("/static", StaticFiles(directory=static_dir), name="static")

    return app_fastapi
