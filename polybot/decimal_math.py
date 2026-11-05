"""Decimal arithmetic helpers for all financial calculations.

POLYBOT uses :class:`decimal.Decimal` exclusively for money, prices, sizes,
fees and risk calculations. Float is never used for financial values.
"""

from __future__ import annotations

from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP, getcontext

# Sufficient precision for crypto pricing and pUSD accounting.
getcontext().prec = 28

ZERO = Decimal("0")
ONE = Decimal("1")
HUNDRED = Decimal("100")

# Standard share/price display precision.
PRICE_QUANT = Decimal("0.001")
SHARE_QUANT = Decimal("0.000001")
AMOUNT_QUANT = Decimal("0.000001")


def dec(value) -> Decimal:
    """Coerce any numeric/string value to Decimal safely."""
    if value is None:
        return ZERO
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def qprice(value: Decimal, tick_size: Optional[Decimal] = None) -> Decimal:
    """Quantize a price to the tick grid (rounds DOWN to a valid tick).

    When ``tick_size`` is provided, snaps to the nearest lower multiple.
    """
    d = dec(value)
    if tick_size:
        t = dec(tick_size)
        if t > ZERO:
            d = (d / t).quantize(Decimal("1"), rounding=ROUND_DOWN) * t
    return d.quantize(PRICE_QUANT, rounding=ROUND_DOWN)


def qshare(value: Decimal) -> Decimal:
    """Quantize share quantity to supported precision (rounds DOWN)."""
    return dec(value).quantize(SHARE_QUANT, rounding=ROUND_DOWN)


def qamount(value: Decimal) -> Decimal:
    """Quantize a USD amount to supported precision (rounds DOWN)."""
    return dec(value).quantize(AMOUNT_QUANT, rounding=ROUND_DOWN)


def round_down(value: Decimal, places: int) -> Decimal:
    quant = Decimal(1).scaleb(-places)
    return dec(value).quantize(quant, rounding=ROUND_DOWN)


def round_half_up(value: Decimal, places: int) -> Decimal:
    quant = Decimal(1).scaleb(-places)
    return dec(value).quantize(quant, rounding=ROUND_HALF_UP)


def pct(value: Decimal) -> Decimal:
    """Express a Decimal ratio as a percentage (x100)."""
    return dec(value) * HUNDRED


def bps(value: Decimal) -> Decimal:
    """Express a Decimal ratio in basis points (x10000)."""
    return dec(value) * Decimal("10000")


def fee_amount(notional: Decimal, rate: Decimal) -> Decimal:
    """Compute a flat fee on a notional amount."""
    return dec(notional) * dec(rate)


def is_positive(value: Decimal) -> bool:
    return dec(value) > ZERO


def is_nonnegative(value: Decimal) -> bool:
    return dec(value) >= ZERO


def safe_divide(numerator: Decimal, denominator: Decimal) -> Decimal:
    """Safe division returning zero on division by zero."""
    denom = dec(denominator)
    if denom == ZERO:
        return ZERO
    return dec(numerator) / denom


def clamp(value: Decimal, minimum: Decimal, maximum: Decimal) -> Decimal:
    return max(min(dec(value), dec(maximum)), dec(minimum))
