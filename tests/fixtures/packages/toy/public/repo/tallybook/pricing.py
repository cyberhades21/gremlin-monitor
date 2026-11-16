"""Price adjustments."""

from decimal import Decimal


def apply_discount(total: Decimal, percent: Decimal) -> Decimal:
    """Reduce `total` by `percent`.

    `percent` must be between 0 and 100 inclusive. Anything else raises ValueError.
    """
    if percent < 0:
        raise ValueError("percent must be between 0 and 100")
    return total - (total * percent / Decimal("100"))
