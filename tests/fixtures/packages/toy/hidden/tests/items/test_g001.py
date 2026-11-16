from decimal import Decimal

from tallybook.limits import is_within_limit


def test_rejects_amount_equal_to_limit():
    assert is_within_limit(Decimal("100"), Decimal("100")) is False
