from decimal import Decimal

from tallybook.limits import is_within_limit


def test_allows_below_limit():
    assert is_within_limit(Decimal("50"), Decimal("100"))


def test_rejects_above_limit():
    assert not is_within_limit(Decimal("150"), Decimal("100"))
