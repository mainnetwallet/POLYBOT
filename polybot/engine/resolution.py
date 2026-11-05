"""Oracle resolution simulator (deterministic).

resolve(market_rules, oracle_observations) -> ResolutionResult.

The result contains opening reference, closing reference, selected
observations, resolution outcome, tie status, resolution confidence and
validation result. Unknown/ambiguous rules => NO TRADE.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Optional

from polybot.clock import parse_iso
from polybot.decimal_math import dec, round_half_up
from polybot.types import ResolutionObservation, ResolutionResult

logger = logging.getLogger("polybot.resolution")


@dataclass
class MarketRules:
    resolution_text: str
    data_source: str
    twap_window: Optional[int]  # seconds
    opening_boundary: Optional[str]   # description
    closing_boundary: Optional[str]
    tie_rules: str
    precision: int = 4
    rounding: str = "half_up"
    methodology: str = "snapshot"   # snapshot | twap_30s | twap_60s | chainlink_twap


@dataclass
class OracleObservations:
    observations: list  # list of dicts: {timestamp, value, source}
    source: str
    freshness_seconds: Optional[int]


def _hash_rules(rules: MarketRules) -> str:
    payload = "|".join([
        rules.resolution_text,
        rules.data_source,
        str(rules.twap_window),
        rules.opening_boundary or "",
        rules.closing_boundary or "",
        rules.tie_rules,
        str(rules.precision),
        rules.rounding,
        rules.methodology,
    ])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def _selected(obs: OracleObservations, rules: MarketRules) -> list[ResolutionObservation]:
    selected: list[ResolutionObservation] = []
    for item in obs.observations:
        ts = parse_iso(item.get("timestamp")) if item.get("timestamp") else None
        value = dec(item.get("value"))
        selected.append(ResolutionObservation(
            timestamp=ts or __import__("datetime").datetime.utcnow(),
            value=value,
            source=item.get("source", obs.source),
            within_window=True,
        ))
    return selected


def resolve(market_rules: MarketRules, oracle_observations: OracleObservations) -> ResolutionResult:
    """Deterministically resolve a market from rules + oracle observations."""
    fingerprint = _hash_rules(market_rules)
    precision = market_rules.precision

    # Unknown or ambiguous rules => NO TRADE.
    if (
        not market_rules.resolution_text
        or not market_rules.data_source
        or market_rules.methodology not in ("snapshot", "twap_30s", "twap_60s", "chainlink_twap")
    ):
        return ResolutionResult(
            opening_reference=Decimal("0"),
            closing_reference=Decimal("0"),
            selected_observations=[],
            outcome=None,
            tie_status=True,
            resolution_confidence=Decimal("0"),
            validation_result="NO_TRADE_AMBIGUOUS_RULES",
            rules_fingerprint=fingerprint,
            metadata={"reason": "unknown or ambiguous resolution rules"},
        )

    # Validate data source is chainlink.
    if market_rules.data_source.lower() != "chainlink":
        return ResolutionResult(
            opening_reference=Decimal("0"),
            closing_reference=Decimal("0"),
            selected_observations=[],
            outcome=None,
            tie_status=True,
            resolution_confidence=Decimal("0"),
            validation_result="NO_TRADE_NON_CHAINLINK_SOURCE",
            rules_fingerprint=fingerprint,
            metadata={"reason": f"resolution source {market_rules.data_source} is not chainlink"},
        )

    selected = _selected(oracle_observations, market_rules)
    if not selected:
        return ResolutionResult(
            opening_reference=Decimal("0"),
            closing_reference=Decimal("0"),
            selected_observations=[],
            outcome=None,
            tie_status=True,
            resolution_confidence=Decimal("0"),
            validation_result="NO_TRADE_NO_OBSERVATIONS",
            rules_fingerprint=fingerprint,
            metadata={"reason": "no oracle observations available"},
        )

    opening = selected[0].value
    closing = selected[-1].value

    if market_rules.methodology in ("twap_60s", "chainlink_twap") and len(selected) > 1:
        # TWAP: time-weighted average of observations.
        values = [o.value for o in selected]
        twap = sum(values) / Decimal(len(values))
        closing_reference = twap
    else:
        closing_reference = closing

    opening_reference = round_half_up(opening, precision)
    closing_reference = round_half_up(closing_reference, precision)

    # Binary outcome: UP if closing >= opening, DOWN otherwise.
    tie = closing_reference == opening_reference
    outcome = None
    if not tie:
        outcome = "UP" if closing_reference > opening_reference else "DOWN"

    confidence = Decimal("0.95") if oracle_observations.freshness_seconds is not None and oracle_observations.freshness_seconds < 300 else Decimal("0.7")

    return ResolutionResult(
        opening_reference=opening_reference,
        closing_reference=closing_reference,
        selected_observations=selected,
        outcome=outcome,
        tie_status=tie,
        resolution_confidence=confidence,
        validation_result="VALID" if not tie else "TIE",
        rules_fingerprint=fingerprint,
        metadata={
            "methodology": market_rules.methodology,
            "n_observations": len(selected),
        },
    )
