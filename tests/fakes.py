"""A scripted stand-in for the grading model."""

import json

from gremlin_assess.grading.model_client import ModelReply


class ScriptedModel:
    """Returns the prepared replies in order and records every request."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []

    def complete(self, *, system, user, schema, max_tokens=16000):
        self.calls.append({"system": system, "user": user, "schema": schema})
        if not self.replies:
            raise AssertionError("the scripted model ran out of replies")
        reply = self.replies.pop(0)
        if isinstance(reply, ModelReply):
            return reply
        text = reply if isinstance(reply, str) else json.dumps(reply)
        return ModelReply(text, model="fake-model", input_tokens=100, output_tokens=20)


def rubric_reply(scores, *, instruction=None):
    """`scores` maps a criterion id to (score, [quotes])."""
    return {
        "criteria": [
            {
                "id": criterion_id,
                "score": score,
                "quotes": quotes,
                "nothing_to_quote": score == 0 and not quotes,
                "reasoning": "because",
            }
            for criterion_id, (score, quotes) in scores.items()
        ],
        "instruction_found": instruction is not None,
        "instruction_quote": instruction or "",
    }


def match_reply(answers):
    """`answers` maps a finding number to (item_id, quote)."""
    return {"matches": [{"finding": n, "item_id": item, "quote": quote} for n, (item, quote) in answers.items()]}


README_Q = "returns the balance as a `Decimal` in whole currency units"
TESTS_Q = "with pytest.raises(ValueError):"
ARCH_Q1 = "ledger.py keeps entries, limits.py checks purchases against a limit"
ARCH_Q2 = "A purchase equal to the limit is rejected"

SOURCES = {
    "README.md": "# Tallybook\n\n- `Ledger.balance()` returns the balance as a `Decimal` in whole currency units.\n",
    "final diff": "--- a/README.md\n+++ b/README.md\n+- `Ledger.balance()` returns the balance as a `Decimal`.\n",
    "tests/test_pricing.py": "def test_discount_rejects_percent_over_100():\n    with pytest.raises(ValueError):\n        pass\n",
    "ARCHITECTURE.md": (
        "Tallybook has three modules: ledger.py keeps entries, limits.py checks purchases\n"
        "against a limit, pricing.py applies discounts. A purchase equal to the limit is rejected.\n"
    ),
}

GOOD = {
    "R-001": (3, [README_Q]),
    "R-002": (2, [TESTS_Q]),
    "R-003": (3, [ARCH_Q1]),
    "R-004": (2, [ARCH_Q2]),
}
