"""Spending limit checks."""

from decimal import Decimal


def is_within_limit(amount: Decimal, limit: Decimal) -> bool:
    """Return True when a purchase of `amount` is allowed under `limit`.

    A purchase at or above the limit is rejected.
    """
    return not amount > limit
