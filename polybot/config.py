"""POLYBOT configuration.

Loads settings from environment variables (``.env``) and
``config/settings.yaml``. Capital remains unset until the user configures it.
All defaults are safe PAPER-mode defaults; nothing authorizes LIVE trading.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Optional

import yaml

from polybot.constants import (
    DEFAULT_DAILY_LOSS_LIMIT,
    DEFAULT_ENTRY_WINDOW_SECONDS,
    DEFAULT_MAX_CONSECUTIVE_LOSSES,
    DEFAULT_MAX_SLIPPAGE,
    DEFAULT_MAX_SPREAD,
    DEFAULT_MIN_NET_EDGE,
    DEFAULT_MIN_PROBABILITY,
    DEFAULT_RISK_PER_TRADE,
    DEFAULT_STOP_LOSS_ROI,
    DEFAULT_TAKE_PROFIT_ROI,
    DEFAULT_TAKER_DELAY_MS,
    DEFAULT_TWAP_FEED,
    MODE,
    ORDER_TYPE_FAK,
)

try:  # PyYAML may be absent in minimal installs
    import yaml as _yaml  # noqa: F401
except Exception:  # pragma: no cover - yaml is a hard dependency
    yaml = None  # type: ignore[assignment]

BASE_DIR = Path(__file__).resolve().parent.parent


def _env(name: str, default: Optional[str] = None) -> Optional[str]:
    return os.environ.get(name, default)


def _env_bool(name: str, default: bool = False) -> bool:
    val = _env(name)
    if val is None:
        return default
    return val.strip().lower() in {"1", "true", "yes", "on", "enabled"}


def _env_decimal(name: str, default: Optional[Decimal] = None) -> Optional[Decimal]:
    val = _env(name)
    if val is None or val.strip() == "":
        return default
    try:
        return Decimal(val)
    except Exception:
        return default


def _env_float(name: str, default: Optional[float] = None) -> Optional[float]:
    val = _env(name)
    if val is None or val.strip() == "":
        return default
    try:
        return float(val)
    except Exception:
        return default


def _env_int(name: str, default: Optional[int] = None) -> Optional[int]:
    val = _env(name)
    if val is None or val.strip() == "":
        return default
    try:
        return int(val)
    except Exception:
        return default


def load_yaml(path: str | Path) -> dict:
    if yaml is None:
        return {}
    p = Path(path)
    if not p.exists():
        return {}
    with p.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    return data if isinstance(data, dict) else {}


@dataclass
class CapitalConfig:
    allocated_usd: Optional[Decimal] = None

    @property
    def configured(self) -> bool:
        return self.allocated_usd is not None and self.allocated_usd > 0


@dataclass
class RiskConfig:
    risk_per_trade: Decimal = Decimal(str(DEFAULT_RISK_PER_TRADE))
    daily_loss_limit: Decimal = Decimal(str(DEFAULT_DAILY_LOSS_LIMIT))
    max_consecutive_losses: int = DEFAULT_MAX_CONSECUTIVE_LOSSES
    daily_profit_lock: Optional[Decimal] = None
    max_active_positions: int = 1
    max_entries_per_market: int = 1


@dataclass
class EntryConfig:
    strategy: str = "dynamic"
    duration_seconds: int = DEFAULT_ENTRY_WINDOW_SECONDS
    min_probability: Decimal = Decimal(str(DEFAULT_MIN_PROBABILITY))
    min_net_edge: Decimal = Decimal(str(DEFAULT_MIN_NET_EDGE))
    max_spread: Decimal = Decimal(str(DEFAULT_MAX_SPREAD))
    max_slippage: Decimal = Decimal(str(DEFAULT_MAX_SLIPPAGE))
    evaluate_every_seconds: int = 1


@dataclass
class ExitConfig:
    strategy: str = "hybrid"
    take_profit_roi: Decimal = Decimal(str(DEFAULT_TAKE_PROFIT_ROI))
    stop_loss_roi: Decimal = Decimal(str(DEFAULT_STOP_LOSS_ROI))
    trailing_stop: bool = False


@dataclass
class FeesConfig:
    read_from_metadata: bool = True
    taker_delay_ms: int = DEFAULT_TAKER_DELAY_MS


@dataclass
class FeedsConfig:
    primary: str = "chainlink"
    secondary: str = "chainlink_redundant"
    twap: str = DEFAULT_TWAP_FEED
    failover: str = "BLOCK"


@dataclass
class GeoConfig:
    check_startup: bool = True
    check_before_live: bool = True
    periodic_seconds: int = 3600


@dataclass
class DashboardConfig:
    host: str = "127.0.0.1"
    port: int = 8000
    admin_user: str = "admin"
    admin_password: Optional[str] = None
    bind_localhost_only: bool = True
    authentication_required: bool = True
    csrf_protection: bool = True


@dataclass
class LiveGateConfig:
    require_explicit_activation: bool = True
    require_geographic_eligibility: bool = True
    require_wallet_auth: bool = True
    require_signature_and_funder_verification: bool = True
    require_resolution_fingerprint_approval: bool = True
    require_resolution_replay_validation: bool = True
    min_replay_markets: int = 20


@dataclass
class HeartbeatConfig:
    enabled: bool = False
    cadence_seconds: int = 5


@dataclass
class Settings:
    mode: str = MODE.PAPER
    target_mode: str = MODE.LIVE
    platform: str = "international_polymarket"
    market: str = "BTC 5M UP/DOWN"
    wallet_address: Optional[str] = None
    private_key: Optional[str] = None
    clob_api_key: Optional[str] = None
    clob_api_secret: Optional[str] = None
    clob_api_passphrase: Optional[str] = None
    relayer_api_key: Optional[str] = None
    relayer_api_key_address: Optional[str] = None
    builder_api_key: Optional[str] = None
    builder_secret: Optional[str] = None
    builder_passphrase: Optional[str] = None
    capital: CapitalConfig = field(default_factory=CapitalConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    entry: EntryConfig = field(default_factory=EntryConfig)
    exit: ExitConfig = field(default_factory=ExitConfig)
    fees: FeesConfig = field(default_factory=FeesConfig)
    feeds: FeedsConfig = field(default_factory=FeedsConfig)
    geo: GeoConfig = field(default_factory=GeoConfig)
    dashboard: DashboardConfig = field(default_factory=DashboardConfig)
    live_gate: LiveGateConfig = field(default_factory=LiveGateConfig)
    heartbeat: HeartbeatConfig = field(default_factory=HeartbeatConfig)
    log_level: str = "INFO"
    primary_feed: str = "chainlink"
    secondary_feed: str = "chainlink_redundant"
    twap_feed: str = DEFAULT_TWAP_FEED

    @property
    def live_activated(self) -> bool:
        return self.mode == MODE.LIVE

    @property
    def capital_configured(self) -> bool:
        return self.capital.configured


def load_settings(env_file: Optional[str | Path] = None) -> Settings:
    """Load settings from .env (optional) and config/settings.yaml."""
    # Load .env manually if python-dotenv is unavailable.
    env_path = Path(env_file) if env_file else BASE_DIR / ".env"
    if env_path.exists():
        try:
            with env_path.open("r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, _, value = line.partition("=")
                    key = key.strip()
                    value = value.strip().strip('"').strip("'")
                    if key and key not in os.environ:
                        os.environ[key] = value
        except Exception:
            pass

    yaml_path = BASE_DIR / "config" / "settings.yaml"
    yaml_data = load_yaml(yaml_path)

    def y(section: str, key: str, default=None):
        return (yaml_data.get(section, {}) or {}).get(key, default)

    settings = Settings()

    # Mode / platform
    settings.mode = _env("POLYBOT_MODE", MODE.PAPER)
    settings.target_mode = _env("POLYBOT_TARGET_MODE", MODE.LIVE)
    settings.platform = _env("POLYBOT_PLATFORM", "international_polymarket")
    settings.market = _env("POLYBOT_MARKET", "BTC 5M UP/DOWN")

    # Wallet / credentials
    settings.wallet_address = _env("POLYMARKET_WALLET_ADDRESS")
    settings.private_key = _env("POLYMARKET_PRIVATE_KEY")
    settings.clob_api_key = _env("POLYMARKET_CLOB_API_KEY")
    settings.clob_api_secret = _env("POLYMARKET_CLOB_API_SECRET")
    settings.clob_api_passphrase = _env("POLYMARKET_CLOB_API_PASSPHRASE")
    settings.relayer_api_key = _env("POLYMARKET_RELAYER_API_KEY")
    settings.relayer_api_key_address = _env("POLYMARKET_RELAYER_API_KEY_ADDRESS")
    settings.builder_api_key = _env("POLYMARKET_BUILDER_API_KEY")
    settings.builder_secret = _env("POLYMARKET_BUILDER_SECRET")
    settings.builder_passphrase = _env("POLYMARKET_BUILDER_PASSPHRASE")

    # Capital
    settings.capital.allocated_usd = _env_decimal("POLYBOT_CAPITAL_USD") or _env_decimal(
        "POLYBOT_ALLOCATED_CAPITAL_USD"
    )
    if settings.capital.allocated_usd is None:
        val = y("capital", "allocated_usd", None)
        if val is not None:
            try:
                settings.capital.allocated_usd = Decimal(str(val))
            except Exception:
                pass

    # Risk
    settings.risk.risk_per_trade = _env_decimal("POLYBOT_RISK_PER_TRADE") or Decimal(
        str(y("risk", "risk_per_trade", DEFAULT_RISK_PER_TRADE))
    )
    settings.risk.daily_loss_limit = _env_decimal("POLYBOT_DAILY_LOSS_LIMIT") or Decimal(
        str(y("risk", "daily_loss_limit", DEFAULT_DAILY_LOSS_LIMIT))
    )
    settings.risk.max_consecutive_losses = _env_int(
        "POLYBOT_MAX_CONSECUTIVE_LOSSES", DEFAULT_MAX_CONSECUTIVE_LOSSES
    )
    settings.risk.max_active_positions = int(
        y("risk", "max_active_positions", 1)
    )
    settings.risk.max_entries_per_market = int(
        y("risk", "max_entries_per_market", 1)
    )
    dpl = y("risk", "daily_profit_lock", None)
    if dpl is not None:
        try:
            settings.risk.daily_profit_lock = Decimal(str(dpl))
        except Exception:
            pass

    # Entry
    settings.entry.strategy = y("entry", "strategy", "dynamic")
    settings.entry.duration_seconds = _env_int(
        "POLYBOT_ENTRY_WINDOW_SECONDS",
        int(y("entry", "duration_seconds", DEFAULT_ENTRY_WINDOW_SECONDS)),
    )
    settings.entry.min_probability = _env_decimal(
        "POLYBOT_MIN_PROBABILITY"
    ) or Decimal(str(y("entry", "min_probability", DEFAULT_MIN_PROBABILITY)))
    settings.entry.min_net_edge = _env_decimal(
        "POLYBOT_MIN_NET_EDGE"
    ) or Decimal(str(y("entry", "min_net_edge", DEFAULT_MIN_NET_EDGE)))
    settings.entry.max_spread = _env_decimal(
        "POLYBOT_MAX_SPREAD"
    ) or Decimal(str(y("entry", "max_spread", DEFAULT_MAX_SPREAD)))
    settings.entry.max_slippage = _env_decimal(
        "POLYBOT_MAX_SLIPPAGE"
    ) or Decimal(str(y("entry", "max_slippage", DEFAULT_MAX_SLIPPAGE)))
    settings.entry.evaluate_every_seconds = int(
        y("entry", "evaluate_every_seconds", 1)
    )

    # Exit
    settings.exit.strategy = y("exit", "strategy", "hybrid")
    settings.exit.take_profit_roi = _env_decimal(
        "POLYBOT_TAKE_PROFIT_ROI"
    ) or Decimal(str(y("exit", "take_profit_roi", DEFAULT_TAKE_PROFIT_ROI)))
    settings.exit.stop_loss_roi = _env_decimal(
        "POLYBOT_STOP_LOSS_ROI"
    ) or Decimal(str(y("exit", "stop_loss_roi", DEFAULT_STOP_LOSS_ROI)))
    settings.exit.trailing_stop = bool(y("exit", "trailing_stop", False))

    # Fees
    settings.fees.taker_delay_ms = _env_int(
        "POLYBOT_TAKER_DELAY_MS", DEFAULT_TAKER_DELAY_MS
    )

    # Feeds
    settings.primary_feed = _env("POLYBOT_PRIMARY_FEED", "chainlink")
    settings.secondary_feed = _env("POLYBOT_SECONDARY_FEED", "chainlink_redundant")
    settings.twap_feed = _env("POLYBOT_TWAP_FEED", DEFAULT_TWAP_FEED)
    settings.feeds.failover = "BLOCK"

    # Geo
    settings.geo.check_startup = _env_bool("POLYBOT_GEO_CHECK_STARTUP", True)
    settings.geo.check_before_live = _env_bool("POLYBOT_GEO_CHECK_BEFORE_LIVE", True)
    settings.geo.periodic_seconds = _env_int("POLYBOT_GEO_PERIODIC_SECONDS", 3600)

    # Dashboard
    settings.dashboard.host = _env("POLYBOT_DASHBOARD_HOST", "127.0.0.1")
    settings.dashboard.port = _env_int("POLYBOT_DASHBOARD_PORT", 8000)
    settings.dashboard.admin_user = _env("POLYBOT_DASHBOARD_ADMIN_USER", "admin")
    settings.dashboard.admin_password = _env("POLYBOT_DASHBOARD_ADMIN_PASSWORD")

    # Live gate
    lg = yaml_data.get("live_gate", {}) or {}
    settings.live_gate.require_explicit_activation = bool(
        lg.get("require_explicit_activation", True)
    )
    settings.live_gate.require_geographic_eligibility = bool(
        lg.get("require_geographic_eligibility", True)
    )
    settings.live_gate.require_wallet_auth = bool(lg.get("require_wallet_auth", True))
    settings.live_gate.require_signature_and_funder_verification = bool(
        lg.get("require_signature_and_funder_verification", True)
    )
    settings.live_gate.require_resolution_fingerprint_approval = bool(
        lg.get("require_resolution_fingerprint_approval", True)
    )
    settings.live_gate.require_resolution_replay_validation = bool(
        lg.get("require_resolution_replay_validation", True)
    )
    settings.live_gate.min_replay_markets = int(
        lg.get("min_replay_markets", 20)
    )

    # Heartbeat
    settings.heartbeat.enabled = _env_bool("POLYBOT_HEARTBEAT_ENABLED", False)
    settings.heartbeat.cadence_seconds = _env_int(
        "POLYBOT_HEARTBEAT_CADENCE_SECONDS", 5
    )

    settings.log_level = _env("POLYBOT_LOG_LEVEL", "INFO")

    return settings


__all__ = ["load_settings", "Settings", "BASE_DIR"]
