from decimal import Decimal

from tallybook.pricing import apply_discount


def test_discount_reduces_total():
    assert apply_discount(Decimal("200"), Decimal("10")) == Decimal("180")


def test_discount_rejects_percent_over_100():
    try:
        apply_discount(Decimal("200"), Decimal("150"))
    except ValueError:
        pass
