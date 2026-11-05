"""Shared data types and dataclasses for POLYBOT.

These types decouple trading logic from SDK-specific types. All financial
values use :class:`decimal.Decimal`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Optional


class MarketVersion(str, Enum):
    V1 = "v1"
    V2 = "v2"


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    GTC = "GTC"
    GTD = "GTD"
    FAK = "FAK"
    FOK = "FOK"


class OrderStatus(str, Enum):
    LIVE = "live"
    MATCHED = "matched"
    DELAYED = "delayed"
    UNMATCHED = "unmatched"
    FILLED = "FILLED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    UNKNOWN = "UNKNOWN"


class TradeSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class Regime(str, Enum):
    TRENDING = "TRENDING"
    SIDEWAYS = "SIDEWAYS"
    HIGH_VOLATILITY = "HIGH_VOLATILITY"
    SUDDEN_REVERSAL = "SUDDEN_REVERSAL"
    UNKNOWN = "UNKNOWN"


class FilterResult(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    UNAVAILABLE = "UNAVAILABLE"


class FeedHealth(str, Enum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    STALE = "STALE"
    DOWN = "DOWN"
    UNVERIFIED = "UNVERIFIED"


class FeatureLevel(str, Enum):
    OFF = "OFF"
    SHADOW = "SHADOW"
    ACTIVE = "ACTIVE"


class Mode(str, Enum):
    PAPER = "PAPER"
    LIVE = "LIVE"


class MarketStateName(str, Enum):
    DISCOVERED = "DISCOVERED"
    ACTIVE = "ACTIVE"
    ENTRY_CLOSED = "ENTRY_CLOSED"
    RESOLVING = "RESOLVING"
    SETTLED = "SETTLED"


class OrderIntentStatus(str, Enum):
    PENDING = "PENDING"
    SUBMITTED = "SUBMITTED"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    FILLED = "FILLED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    UNKNOWN = "UNKNOWN"
    RESOLVED = "RESOLVED"


class ExitPriority(int, Enum):
    EMERGENCY = 0
    STOP_LOSS = 1
    TRAILING_STOP = 2
    TAKE_PROFIT = 3
    PROBABILITY_REVERSAL = 4
    EV_COMPARISON = 5
    RESOLUTION = 6


# ---------------------------------------------------------------------------
# Market discovery metadata
# ---------------------------------------------------------------------------
@dataclass
class MarketOutcomeId:
    label: str
    token_id: Optional[str] = None      # V1 clobTokenIds
    position_id: Optional[str] = None   # V2 positionIds


@dataclass
class MarketTradingConstraints:
    minimum_tick_size: Optional[Decimal] = None
    minimum_order_size: Optional[Decimal] = None
    fees_enabled: bool = False
    fee_rate: Optional[Decimal] = None
    fee_exponent: Optional[float] = None
    fee_taker_only: bool = True
    fee_rebate_rate: Optional[Decimal] = None
    seconds_delay: Optional[int] = None


@dataclass
class MarketMetadata:
    market_id: str
    slug: Optional[str]
    question: Optional[str]
    condition_id: Optional[str]
    version: MarketVersion
    state_active: bool = False
    state_closed: bool = False
    state_archived: bool = False
    accepting_orders: bool = False
    enable_order_book: bool = False
    neg_risk: bool = False
    restricted: bool = False
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    closed_time: Optional[datetime] = None
    event_start_time: Optional[datetime] = None   # correct trading-window start
    outcomes: list[MarketOutcomeId] = field(default_factory=list)
    outcome_prices: list[Decimal] = field(default_factory=list)
    trading: MarketTradingConstraints = field(default_factory=MarketTradingConstraints)
    metrics_liquidity: Optional[Decimal] = None
    metrics_volume_24hr: Optional[Decimal] = None
    prices_best_bid: Optional[Decimal] = None
    prices_best_ask: Optional[Decimal] = None
    prices_spread: Optional[Decimal] = None
    last_trade_price: Optional[Decimal] = None
    rewards_min_size: Optional[Decimal] = None
    rewards_max_spread: Optional[float] = None
    raw: dict = field(default_factory=dict)

    @property
    def trade_ready(self) -> bool:
        return (
            self.state_active
            and not self.state_closed
            and not self.state_archived
            and self.accepting_orders
        )

    def asset_id_for_outcome(self, label: str) -> Optional[str]:
        for outcome in self.outcomes:
            if outcome.label.lower() == label.lower():
                if self.version == MarketVersion.V2:
                    return outcome.position_id
                return outcome.token_id
        return None


# ---------------------------------------------------------------------------
# Market data ticks
# ---------------------------------------------------------------------------
@dataclass
class BTCTick:
    timestamp: datetime
    price: Decimal
    source: str
    provider: str
    sequence: Optional[int] = None
    data_age_ms: Optional[int] = None
    quality_flags: list[str] = field(default_factory=list)
    raw: dict = field(default_factory=dict)


@dataclass
class TWAPTick:
    timestamp: datetime
    value: Decimal
    window_seconds: int
    source: str
    provider: str = "chainlink"
    observation_timestamp: Optional[datetime] = None
    remaining_window_seconds: Optional[int] = None
    data_age_ms: Optional[int] = None
    quality_flags: list[str] = field(default_factory=list)
    raw: dict = field(default_factory=dict)


@dataclass
class OrderBookSnapshot:
    timestamp: datetime
    asset_id: str
    bids: list[tuple[Decimal, Decimal]] = field(default_factory=list)  # (price, size)
    asks: list[tuple[Decimal, Decimal]] = field(default_factory=list)
    best_bid: Optional[Decimal] = None
    best_ask: Optional[Decimal] = None
    spread: Optional[Decimal] = None
    min_order_size: Optional[Decimal] = None
    tick_size: Optional[Decimal] = None
    hash: Optional[str] = None
    source: str = "polymarket_clob"
    raw: dict = field(default_factory=dict)


@dataclass
class MarketDataRecord:
    """A complete market snapshot recorded for analytics."""
    timestamp: datetime
    market_id: str
    condition_id: Optional[str]
    btc_price: Optional[Decimal]
    btc_source: Optional[str]
    twap: Optional[Decimal]
    twap_window_seconds: Optional[int]
    up_price: Optional[Decimal]
    down_price: Optional[Decimal]
    best_bid: Optional[Decimal]
    best_ask: Optional[Decimal]
    spread: Optional[Decimal]
    liquidity: Optional[Decimal]
    implied_probability_up: Optional[Decimal]
    expected_executable_price: Optional[Decimal]
    data_source: str = "polymarket"
    quality_flags: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Prediction / signal outputs
# ---------------------------------------------------------------------------
@dataclass
class ProbabilityOutput:
    p_up: Decimal
    p_down: Decimal
    confidence: Decimal
    uncertainty_interval: tuple[Decimal, Decimal]
    expected_settlement_outcome: Optional[str]  # "UP" / "DOWN" / None
    regime: Regime
    signal_quality: Decimal
    metadata: dict = field(default_factory=dict)


@dataclass
class RegimeOutput:
    regime: Regime
    momentum: Decimal
    trend_strength: Decimal
    volatility: Decimal
    acceleration: Decimal
    multi_timeframe_agreement: Decimal
    reversal_detected: bool
    hysteresis_applied: bool
    metadata: dict = field(default_factory=dict)


@dataclass
class FilterDecision:
    name: str
    result: FilterResult
    reason: str
    evidence: dict = field(default_factory=dict)
    timestamp: Optional[datetime] = None


# ---------------------------------------------------------------------------
# Entry / EV
# ---------------------------------------------------------------------------
@dataclass
class EntryDecision:
    eligible: bool
    side: Optional[TradeSide]
    probability: Decimal
    net_edge: Decimal
    expected_net_value: Decimal
    entry_price: Optional[Decimal]
    size_shares: Optional[Decimal]
    size_cost_usd: Optional[Decimal]
    spread: Optional[Decimal]
    slippage_estimate: Optional[Decimal]
    fee_estimate: Optional[Decimal]
    remaining_window_seconds: Optional[Decimal]
    no_trade_reason: Optional[str] = None
    gates_passed: list[str] = field(default_factory=list)
    gates_failed: list[str] = field(default_factory=list)


@dataclass
class PositionSizingResult:
    size_shares: Decimal
    size_cost_usd: Decimal
    risk_usd: Decimal
    max_allowed_by_risk: Decimal
    max_allowed_by_balance: Decimal
    min_order_size_usd: Decimal
    within_risk_budget: bool
    meets_minimum_order: bool
    veto_reason: Optional[str] = None


# ---------------------------------------------------------------------------
# Position / exit
# ---------------------------------------------------------------------------
@dataclass
class Position:
    market_id: str
    condition_id: Optional[str]
    asset_id: str
    side: TradeSide
    entry_price: Decimal
    quantity_shares: Decimal
    cost_usd: Decimal
    current_price: Optional[Decimal] = None
    unrealized_pnl_usd: Optional[Decimal] = None
    unrealized_roi: Optional[Decimal] = None
    take_profit_price: Optional[Decimal] = None
    stop_loss_price: Optional[Decimal] = None
    trailing_stop_price: Optional[Decimal] = None
    exit_state: str = "OPEN"   # OPEN / EXITING / EXITED / DUST / HOLD_TO_RESOLUTION
    dust: bool = False
    residual_shares: Optional[Decimal] = None
    entry_order_id: Optional[str] = None
    filled_at: Optional[datetime] = None
    metadata: dict = field(default_factory=dict)


@dataclass
class ExitDecision:
    action: str                  # NONE / TAKE_PROFIT / STOP_LOSS / TRAILING_STOP / ...
    priority: ExitPriority
    price: Optional[Decimal]
    size_shares: Optional[Decimal]
    reason: str
    executable: bool = False


# ---------------------------------------------------------------------------
# Risk / exposure
# ---------------------------------------------------------------------------
@dataclass
class ExposureSnapshot:
    timestamp: datetime
    available_balance_usd: Decimal
    allocated_capital_usd: Decimal
    reserved_capital_usd: Decimal
    exposure_usd: Decimal
    daily_pnl_usd: Decimal
    daily_loss_usd: Decimal
    open_positions: int
    pending_orders: int
    risk_budget_remaining_usd: Decimal
    daily_loss_limit_remaining_usd: Decimal
    within_limits: bool
    blocking_reason: Optional[str] = None


# ---------------------------------------------------------------------------
# Resolution simulation
# ---------------------------------------------------------------------------
@dataclass
class ResolutionObservation:
    timestamp: datetime
    value: Decimal
    source: str
    within_window: bool = True


@dataclass
class ResolutionResult:
    opening_reference: Decimal
    closing_reference: Decimal
    selected_observations: list[ResolutionObservation]
    outcome: Optional[str]        # "UP" / "DOWN" / None
    tie_status: bool = False
    resolution_confidence: Decimal = Decimal("0")
    validation_result: str = "UNVERIFIED"
    rules_fingerprint: Optional[str] = None
    metadata: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Heartbeat
# ---------------------------------------------------------------------------
@dataclass
class HeartbeatState:
    latest_id: Optional[str] = None
    last_sent: Optional[datetime] = None
    cadence_seconds: int = 5
    enabled: bool = False
    healthy: bool = False
    failures: int = 0
    cancellation_scope: str = "api_key"


# ---------------------------------------------------------------------------
# Order journal entry
# ---------------------------------------------------------------------------
@dataclass
class OrderJournalEntry:
    idempotency_key: str
    market_id: str
    condition_id: Optional[str]
    asset_id: str
    side: TradeSide
    price: Decimal
    size_shares: Decimal
    order_type: OrderType
    strategy_version: str
    timestamp: datetime
    status: OrderIntentStatus = OrderIntentStatus.PENDING
    order_id: Optional[str] = None
    filled_shares: Decimal = Decimal("0")
    rejection_reason: Optional[str] = None
    reconciliation_notes: Optional[str] = None
