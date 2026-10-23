"""
Shared types used across all domain layers.

Design decision: Define primitive type aliases here to:
1. Provide semantic meaning (Price vs. Decimal)
2. Enable easy global refactoring
3. Avoid float precision bugs in financial calculations
"""

from decimal import Decimal
from uuid import UUID  # noqa: F401 - re-exported for convenience

# ---------------------------------------------------------------------------
# Financial primitives - always use Decimal for money/prices
# ---------------------------------------------------------------------------
type Price = Decimal
type Volume = Decimal
type Percentage = Decimal  # 0.0 to 100.0
type BasisPoints = int  # 100 bps = 1%
type Confidence = float  # 0.0 to 1.0

# ---------------------------------------------------------------------------
# Market identifiers
# ---------------------------------------------------------------------------
type SymbolStr = str  # e.g., "BTC/USDT"
type ExchangeId = str  # e.g., "binance", "bybit"
type TimeframeStr = str  # e.g., "1m", "5m", "1h", "1d"

# ---------------------------------------------------------------------------
# Numeric
# ---------------------------------------------------------------------------
type Milliseconds = int
type Seconds = int
type UnixTimestamp = int  # Unix timestamp in seconds

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DECIMAL_ZERO = Decimal("0")
DECIMAL_ONE = Decimal("1")
DECIMAL_HUNDRED = Decimal("100")
MAX_CONFIDENCE: float = 1.0
MIN_CONFIDENCE: float = 0.0
