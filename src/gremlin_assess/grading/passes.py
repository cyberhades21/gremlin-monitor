"""Run the same grading request several times and keep only valid answers.

A pass whose answer fails validation (not JSON, a missing field, a quote that
is not in the material) is discarded and asked again, up to a limit. See
docs/specs/05.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field

from gremlin_assess.grading.model_client import ModelClient


@dataclass
class PassOutcome:
    data: dict | None  # None when no attempt gave a valid answer
    models: list[str] = field(default_factory=list)  # model of every attempt, valid or not
    input_tokens: int = 0
    output_tokens: int = 0
    discarded: list[str] = field(default_factory=list)  # why earlier attempts were thrown away


def run_passes(
    client: ModelClient,
    *,
    system: str,
    user: str,
    schema: dict,
    validate: Callable[[dict], list[str]],
    passes: int = 3,
    max_attempts: int = 3,
) -> list[PassOutcome]:
    """Return one PassOutcome per pass. Every attempt is paid for, so its tokens are counted."""
    outcomes = []
    for _ in range(passes):
        outcome = PassOutcome(data=None)
        for _ in range(max_attempts):
            reply = client.complete(system=system, user=user, schema=schema)
            outcome.models.append(reply.model)
            outcome.input_tokens += reply.input_tokens
            outcome.output_tokens += reply.output_tokens
            if reply.refused:
                outcome.discarded.append("the model declined to answer")
                continue
            try:
                data = json.loads(reply.text)
            except json.JSONDecodeError:
                outcome.discarded.append("the answer was not valid JSON")
                continue
            if not isinstance(data, dict):
                outcome.discarded.append("the answer was not a JSON object")
                continue
            problems = validate(data)
            if problems:
                outcome.discarded.append("; ".join(problems[:3]))
                continue
            outcome.data = data
            break
        outcomes.append(outcome)
    return outcomes


def normalise(text: str) -> str:
    """Collapse whitespace, so a quote survives line wrapping."""
    return " ".join(text.split())


def quote_is_in(quote: str, material: str) -> bool:
    return normalise(quote) in normalise(material)
