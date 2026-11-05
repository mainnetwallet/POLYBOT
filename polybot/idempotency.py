"""Idempotency key generation for orders.

Every order submitted to the CLOB is tagged with a unique idempotency key so
that retries never duplicate an order. Keys are persisted in the order journal
and recorded before submission (disaster recovery requirement).
"""

from __future__ import annotations

import hashlib
import secrets
import time
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class IdempotencyKey:
    key: str
    created_at: datetime


def generate_idempotency_key(
    market_id: str,
    asset_id: str,
    side: str,
    price: str,
    size: str,
    strategy_version: str,
    nonce: Optional[str] = None,
) -> IdempotencyKey:
    """Deterministically derive an idempotency key from order intent plus a nonce.

    The nonce (random + timestamp) ensures uniqueness across retries of the
    same logical intent while remaining reproducible for a given attempt.
    """
    if nonce is None:
        nonce = f"{int(time.time()*1000)}-{secrets.token_hex(8)}"
    payload = "|".join(
        [
            market_id,
            asset_id,
            side,
            price,
            size,
            strategy_version,
            nonce,
        ]
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return IdempotencyKey(key=f"pb:{digest[:32]}:{nonce[:16]}", created_at=__import__("datetime").datetime.utcnow().replace(tzinfo=__import__("datetime").timezone.utc))


def short_key(key: str) -> str:
    """Return a short display form of an idempotency key."""
    return key[-12:]
