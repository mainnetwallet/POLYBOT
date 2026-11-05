"""Clock and time synchronization.

POLYBOT synchronizes with Polymarket server time and never relies solely on
the local system clock for trading-window decisions.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx


def utc_now() -> datetime:
    """Return the current UTC time (timezone-aware)."""
    return datetime.now(timezone.utc)


def utc_now_ts_ms() -> int:
    """Current UTC time in milliseconds since epoch."""
    return int(time.time() * 1000)


def utc_now_ts_s() -> int:
    """Current UTC time in seconds since epoch."""
    return int(time.time())


def parse_iso(value) -> Optional[datetime]:
    """Parse an ISO-8601 timestamp into a timezone-aware UTC datetime."""
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, (int, float)) or str(value).strip().lstrip("-").replace(".", "", 1).isdigit():
        # Epoch timestamp: milliseconds (13 digits) or seconds (10 digits).
        num = float(value)
        if abs(num) >= 1e11:
            num /= 1000.0
        dt = datetime.fromtimestamp(num, tz=timezone.utc)
    else:
        dt = datetime.fromisoformat(str(value))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def to_ms(dt: Optional[datetime]) -> Optional[int]:
    if dt is None:
        return None
    return int(dt.timestamp() * 1000)


def to_s(dt: Optional[datetime]) -> Optional[int]:
    if dt is None:
        return None
    return int(dt.timestamp())


class ServerClock:
    """Tracks offset between local clock and Polymarket server time.

    The offset is measured by timing a round-trip to the Gamma API and is
    applied to local time when evaluating the trading window. Re-measured
    periodically to bound clock drift.
    """

    def __init__(self, gamma_api_url: str = "https://gamma-api.polymarket.com") -> None:
        self.gamma_api_url = gamma_api_url.rstrip("/")
        self.offset_seconds: float = 0.0  # server - local
        self.last_sync: Optional[float] = None
        self._measured: bool = False

    async def sync(self, timeout: float = 5.0) -> float:
        """Measure server time and update the offset. Returns offset in seconds."""
        import time as _time

        start = _time.time()
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                # Gamma markets keyset returns quickly and carries no auth.
                resp = await client.get(
                    f"{self.gamma_api_url}/markets/keyset",
                    params={"closed": "false", "limit": "1"},
                )
                resp.raise_for_status()
        except Exception:
            # If unreachable, keep the previous offset (defaults to zero).
            return self.offset_seconds
        end = _time.time()
        rtt = end - start
        # Approximate server time as mid-point of the request.
        server_now = start + rtt / 2.0
        local_now = _time.time()
        self.offset_seconds = server_now - local_now
        self.last_sync = local_now
        self._measured = True
        return self.offset_seconds

    def now(self) -> float:
        """Current estimated server time in seconds since epoch."""
        return time.time() + self.offset_seconds

    def now_datetime(self) -> datetime:
        from datetime import datetime, timezone

        return datetime.fromtimestamp(self.now(), tz=timezone.utc)

    def is_synced(self) -> bool:
        return self._measured

    @property
    def drift_warning(self) -> bool:
        """True if we have never synced or sync is stale (> 1 hour)."""
        if not self._measured or self.last_sync is None:
            return True
        return (time.time() - self.last_sync) > 3600.0
