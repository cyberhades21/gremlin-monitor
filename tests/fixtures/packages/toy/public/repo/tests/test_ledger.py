from decimal import Decimal

from tallybook.ledger import Ledger


def test_add_entry_and_balance():
    ledger = Ledger()
    ledger.add_entry("coffee", "3.50")
    ledger.add_entry("book", "12.00")
    assert ledger.balance() == Decimal("15.50")


def test_balance_is_decimal():
    assert isinstance(Ledger().balance(), Decimal)
