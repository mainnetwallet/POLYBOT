"""Geo restriction check.

Checks the official geographic restriction endpoint at startup, before LIVE
activation, and periodically. Never bypassed via VPN/proxy. Defaults to
UNVERIFIED (BLOCK) until the endpoint returns a definitive eligible result.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

import httpx

from polybot.constants import GEO_CHECK_ENDPOINT

logger = logging.getLogger("polybot.geo")


class GeoChecker:
    def __init__(self, endpoint: Optional[str] = None, timeout: float = 8.0) -> None:
        self.endpoint = endpoint or GEO_CHECK_ENDPOINT
        self.timeout = timeout
        self._last_result: Optional[bool] = None
        self._last_reason: str = "unchecked"
        self._last_checked: Optional[float] = None

    async def is_eligible(self, force: bool = False) -> tuple[bool, str]:
        now = __import__("time").time()
        if (
            not force
            and self._last_result is not None
            and self._last_checked is not None
            and (now - self._last_checked) < 300
        ):
            return self._last_result, self._last_reason

        # The official public geo endpoint is not documented in the open
        # Polymarket docs; where unavailable, default to UNVERIFIED/BLOCK.
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.get(self.endpoint)
                if resp.status_code != 200:
                    self._set(False, f"geo endpoint HTTP {resp.status_code}")
                    return False, self._last_reason
                data = resp.json()
                eligible = bool(data.get("eligible", data.get("allowed", False)))
                self._set(eligible, "geo endpoint returned eligible" if eligible else "geo endpoint returned ineligible")
                return eligible, self._last_reason
        except Exception as exc:  # pragma: no cover - network
            self._set(False, f"geo check unverified: {exc}")
            return False, self._last_reason

    def _set(self, eligible: bool, reason: str) -> None:
        self._last_result = eligible
        self._last_reason = reason
        self._last_checked = __import__("time").time()

    def status(self) -> dict:
        return {"eligible": self._last_result, "reason": self._last_reason}
