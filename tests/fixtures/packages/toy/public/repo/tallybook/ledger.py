"""In-memory ledger of entries."""

from dataclasses import dataclass, field
from decimal import Decimal


@dataclass(frozen=True)
class Entry:
    description: str
    amount: Decimal
    tags: tuple[str, ...] = ()


@dataclass
class Ledger:
    entries: list[Entry] = field(default_factory=list)

    def add_entry(self, description: str, amount: str | Decimal, tags: tuple[str, ...] = ()) -> Entry:
        """Add an entry and return it. Amounts are in whole currency units."""
        entry = Entry(description, Decimal(str(amount)), tuple(tags))
        self.entries.append(entry)
        return entry

    def balance(self) -> Decimal:
        """Return the sum of all entry amounts as a Decimal in currency units."""
        return sum((entry.amount for entry in self.entries), Decimal("0"))
