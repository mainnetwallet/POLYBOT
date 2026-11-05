"""POLYBOT core constants.

All contract addresses, chain IDs, domains, signatures, and state-machine
values are defined here. Values are taken from the official Polymarket
documentation (https://docs.polymarket.com/) and the polymarket-client 0.16.0 SDK.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Chain / network
# ---------------------------------------------------------------------------
CHAIN_ID = 137  # Polygon

# ---------------------------------------------------------------------------
# Exchange / collateral contracts (official addresses)
# ---------------------------------------------------------------------------
# pUSD collateral token
PUSD_TOKEN_ADDRESS = "0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB"
# Position Manager (V2 outcome-token ledger)
POSITION_MANAGER_ADDRESS = "0x006F54F7f9A22e0000CC2AB60031000000ae9fEF"
# Conditional Tokens (CTF outcome-token ledger)
CONDITIONAL_TOKENS_ADDRESS = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
# Polymarket Protocol V2 Exchange
PROTOCOL_V2_EXCHANGE_ADDRESS = "0xe3333700cA9d93003F00f0F71f8515005F6c00Aa"
# Standard (CTF) Exchange
STANDARD_EXCHANGE_ADDRESS = "0xE111180000d2663C0091e4f400237545B87B996B"
# Neg Risk Exchange
NEG_RISK_EXCHANGE_ADDRESS = "0xe2222d279d744050d28e00520010520000310F59"
# Neg Risk Adapter
NEG_RISK_ADAPTER_ADDRESS = "0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296"
# Deposit Wallet factory
WALLET_FACTORY_ADDRESS = "0x00000000000Fb5C9ADea0298D729A0CB3823Cc07"

# ---------------------------------------------------------------------------
# Signing domains (EIP-712). Market protocol version (v1/v2) is distinct
# from the signing domain version.
# ---------------------------------------------------------------------------
DOMAIN_NAME = "Polymarket CTF Exchange"
DOMAIN_V2_VERSION = "3"   # Polymarket Protocol V2 markets
DOMAIN_CTF_VERSION = "2"  # CTF (V1) markets

# ---------------------------------------------------------------------------
# Wallet signature types (matches polymarket-client WalletType)
# ---------------------------------------------------------------------------
SIG_TYPE_EOA = 0
SIG_TYPE_PROXY = 1
SIG_TYPE_SAFE = 2
SIG_TYPE_DEPOSIT_WALLET = 3

SIGNATURE_TYPE_NAMES = {
    SIG_TYPE_EOA: "EOA",
    SIG_TYPE_PROXY: "Proxy",
    SIG_TYPE_SAFE: "Safe",
    SIG_TYPE_DEPOSIT_WALLET: "Deposit Wallet",
}

# ---------------------------------------------------------------------------
# Order side
# ---------------------------------------------------------------------------
SIDE_BUY = 0
SIDE_SELL = 1
SIDE_NAMES = {SIDE_BUY: "BUY", SIDE_SELL: "SELL"}

# ---------------------------------------------------------------------------
# Market protocol versions
# ---------------------------------------------------------------------------
MARKET_VERSION_V1 = "v1"  # CTF / clobTokenIds
MARKET_VERSION_V2 = "v2"  # position-based / positionIds
SUPPORTED_MARKET_VERSIONS = {MARKET_VERSION_V1, MARKET_VERSION_V2}

# ---------------------------------------------------------------------------
# Order types / lifetimes
# ---------------------------------------------------------------------------
ORDER_TYPE_GTC = "GTC"
ORDER_TYPE_GTD = "GTD"
ORDER_TYPE_FAK = "FAK"
ORDER_TYPE_FOK = "FOK"

ORDER_STATUS_LIVE = "live"
ORDER_STATUS_MATCHED = "matched"
ORDER_STATUS_DELAYED = "delayed"
ORDER_STATUS_UNMATCHED = "unmatched"

# GTD minimum effective lifetime (seconds): must be >= 3 min in future and
# expires 1 min before stated, so minimum effective lifetime is ~2 minutes.
GTD_MIN_EXPIRATION_OFFSET = 180  # seconds
GTD_SECURITY_THRESHOLD = 60      # seconds

# ---------------------------------------------------------------------------
# Taker delay (conservative default until empirically resolved)
# ---------------------------------------------------------------------------
DEFAULT_TAKER_DELAY_MS = 250

# ---------------------------------------------------------------------------
# Entry window
# ---------------------------------------------------------------------------
DEFAULT_ENTRY_WINDOW_SECONDS = 120

# ---------------------------------------------------------------------------
# Risk defaults (configurable, never hardcoded limits for LIVE)
# ---------------------------------------------------------------------------
DEFAULT_RISK_PER_TRADE = 0.02      # 2% max planned loss per trade
DEFAULT_DAILY_LOSS_LIMIT = 0.04    # 4% max daily loss
DEFAULT_MAX_CONSECUTIVE_LOSSES = 2

# ---------------------------------------------------------------------------
# Entry strategy initial research parameters (unvalidated)
# ---------------------------------------------------------------------------
DEFAULT_MIN_PROBABILITY = 0.60
DEFAULT_MIN_NET_EDGE = 0.05        # 5 percentage points
DEFAULT_MAX_SPREAD = 0.03          # $0.03
DEFAULT_MAX_SLIPPAGE = 0.01        # $0.01

# ---------------------------------------------------------------------------
# Exit strategy initial research parameters (unvalidated)
# ---------------------------------------------------------------------------
DEFAULT_TAKE_PROFIT_ROI = 0.20     # +20% ROI
DEFAULT_STOP_LOSS_ROI = 0.25       # -25% ROI
DEFAULT_TWAP_FEED = "chainlink_twap_60s"

# ---------------------------------------------------------------------------
# Market state machine (persistent)
# ---------------------------------------------------------------------------
class MarketState:
    DISCOVERED = "DISCOVERED"
    ACTIVE = "ACTIVE"
    ENTRY_CLOSED = "ENTRY_CLOSED"
    RESOLVING = "RESOLVING"
    SETTLED = "SETTLED"

    ALL = (DISCOVERED, ACTIVE, ENTRY_CLOSED, RESOLVING, SETTLED)

    TRANSITIONS = {
        DISCOVERED: (ACTIVE,),
        ACTIVE: (ENTRY_CLOSED, RESOLVING),
        ENTRY_CLOSED: (RESOLVING,),
        RESOLVING: (SETTLED,),
        SETTLED: (),
    }

    @classmethod
    def valid_transition(cls, frm: str, to: str) -> bool:
        return to in cls.TRANSITIONS.get(frm, ())


# ---------------------------------------------------------------------------
# Feed health states
# ---------------------------------------------------------------------------
class FeedHealth:
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    STALE = "STALE"
    DOWN = "DOWN"
    UNVERIFIED = "UNVERIFIED"

    ALL = (HEALTHY, DEGRADED, STALE, DOWN, UNVERIFIED)


# ---------------------------------------------------------------------------
# Feature activation levels
# ---------------------------------------------------------------------------
class FeatureLevel:
    OFF = "OFF"
    SHADOW = "SHADOW"
    ACTIVE = "ACTIVE"

    ALL = (OFF, SHADOW, ACTIVE)


# ---------------------------------------------------------------------------
# Trade side (business)
# ---------------------------------------------------------------------------
class TradeSide:
    BUY = "BUY"
    SELL = "SELL"


# ---------------------------------------------------------------------------
# Order intent status (journal)
# ---------------------------------------------------------------------------
class OrderIntentStatus:
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


# ---------------------------------------------------------------------------
# Operating modes
# ---------------------------------------------------------------------------
class Mode:
    PAPER = "PAPER"
    LIVE = "LIVE"


MODE = Mode


# ---------------------------------------------------------------------------
# PolyBolt / feed endpoints
# ---------------------------------------------------------------------------
POLYBOLT_WS_URL = "wss://ws-live-v2.polymarket.com/ws"
CLOB_MARKET_WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
CLOB_USER_WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/user"
GAMMA_API_URL = "https://gamma-api.polymarket.com"
CLOB_API_URL = "https://clob.polymarket.com"
RELAYER_API_URL = "https://relayer-v2.polymarket.com"

# PolyBolt channels
CHANNEL_PRICE_CRYPTO = "price.crypto"
CHANNEL_PRICE_CRYPTO_TWAP = "price.crypto.twap"

# Default BTC symbol (lowercase USD pair per PolyBolt spec)
BTC_SYMBOL = "btcusd"
TWAP_WINDOW_SECONDS = 60

# ---------------------------------------------------------------------------
# Geo restriction
# ---------------------------------------------------------------------------
GEO_CHECK_ENDPOINT = "https://data-api.polymarket.com/v2/geo/check"  # to be verified

# ---------------------------------------------------------------------------
# Database / storage
# ---------------------------------------------------------------------------
DB_WAL_JOURNAL_MODE = "WAL"
DB_SYNC_MODE = "NORMAL"

RETENTION_DAYS_MARKET_HISTORY = 90
RETENTION_DAYS_TICKS = 30

# ---------------------------------------------------------------------------
# Live gate requirement count (spec section 36: 23 conditions)
# ---------------------------------------------------------------------------
LIVE_GATE_CONDITION_COUNT = 23

# ---------------------------------------------------------------------------
# Resolution replay validation
# ---------------------------------------------------------------------------
MIN_REPLAY_MARKETS = 20
