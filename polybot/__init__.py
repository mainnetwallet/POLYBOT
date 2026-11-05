"""POLYBOT package initializer.

Autonomous Polymarket BTC 5-minute UP/DOWN trading application.

WARNING: LIVE trading is locked by default. The strict LIVE gate (section 36)
must be satisfied and explicitly activated by the user before any real-money
order is ever sent.
"""

from __future__ import annotations

__version__ = "1.0.0"
__app__ = "POLYBOT"

# Default operating mode is PAPER until the user explicitly activates LIVE.
DEFAULT_MODE = "PAPER"

__all__ = ["__version__", "__app__", "DEFAULT_MODE"]
