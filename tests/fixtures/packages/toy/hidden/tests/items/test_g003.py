from decimal import Decimal

import pytest

from tallybook.pricing import apply_discount


def test_rejects_percent_over_100():
    with pytest.raises(ValueError):
        apply_discount(Decimal("200"), Decimal("150"))
