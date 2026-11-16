"""Behaviour that must keep working whatever the candidate changes."""

from decimal import Decimal

import pytest

from tallybook.ledger import Ledger
from tallybook.limits import is_within_limit
from tallybook.pricing import apply_discount


def test_balance_sums_entries():
    ledger = Ledger()
    ledger.add_entry("coffee", "3.50")
    ledger.add_entry("book", "12.00")
    assert ledger.balance() == Decimal("15.50")


def test_amount_below_limit_is_allowed():
    assert is_within_limit(Decimal("50"), Decimal("100")) is True


def test_amount_above_limit_is_rejected():
    assert is_within_limit(Decimal("150"), Decimal("100")) is False


def test_ten_percent_discount():
    assert apply_discount(Decimal("200"), Decimal("10")) == Decimal("180")


def test_negative_percent_is_rejected():
    with pytest.raises(ValueError):
        apply_discount(Decimal("200"), Decimal("-5"))
