from decimal import Decimal

from tallybook.ledger import Ledger


def test_total_by_tag():
    ledger = Ledger()
    ledger.add_entry("coffee", "3.50", tags=("food",))
    ledger.add_entry("book", "12.00", tags=("fun",))
    ledger.add_entry("lunch", "9.00", tags=("food",))
    assert ledger.total_by_tag("food") == Decimal("12.50")


def test_total_by_unknown_tag_is_zero():
    assert Ledger().total_by_tag("none") == Decimal("0")
