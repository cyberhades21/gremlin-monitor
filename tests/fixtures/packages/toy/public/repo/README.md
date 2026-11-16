# Tallybook

A small ledger for tracking spending.

## Usage

```python
from tallybook import Ledger

ledger = Ledger()
ledger.add_entry("coffee", "3.50")
print(ledger.balance())
```

## Rules

- A purchase at or above the spending limit is rejected.
- Discounts must be between 0 and 100 percent.
- `Ledger.balance()` returns the balance in cents as an integer.
