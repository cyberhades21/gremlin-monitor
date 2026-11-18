"""Calls to the grading model. See docs/specs/05.

Everything that talks to a model goes through ModelClient, so tests use a
scripted fake and the real client stays a thin wrapper.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

# One pinned model for every grading pass. See docs/specs/00, decision D14.
PINNED_MODEL = "claude-opus-5-5"


@dataclass(frozen=True)
class ModelReply:
    text: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    refused: bool = False


class ModelClient(Protocol):
    def complete(self, *, system: str, user: str, schema: dict, max_tokens: int = 16000) -> ModelReply:
        """Send one request and return the reply. `schema` is a JSON schema for the answer."""


class UsageCounter:
    """Wraps a client and adds up what every call through it used."""

    def __init__(self, client: ModelClient):
        self._client = client
        self.calls = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.models: set[str] = set()

    def complete(self, *, system: str, user: str, schema: dict, max_tokens: int = 16000) -> ModelReply:
        reply = self._client.complete(system=system, user=user, schema=schema, max_tokens=max_tokens)
        self.calls += 1
        self.input_tokens += reply.input_tokens
        self.output_tokens += reply.output_tokens
        self.models.add(reply.model)
        return reply

    def to_dict(self) -> dict:
        return {
            "calls": self.calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "models": sorted(self.models),
        }


class AnthropicModelClient:
    """The real client. It needs `uv sync --extra model` and ANTHROPIC_API_KEY.

    No automated test covers it, because that would need a key and spend money.

    Choices that matter for grading:
    - One pinned model. Server-side refusal fallbacks are NOT enabled, because
      a pass quietly graded by another model would break the rule that every
      pass records the model that produced it. A refusal is returned as a
      refused reply, and the caller discards the pass.
    - Thinking is adaptive, which is the only mode this model accepts, and
      effort is set explicitly because the model's default is lower.
    - No sampling parameters, which this model rejects.
    """

    def __init__(self, model: str = PINNED_MODEL, effort: str = "high", client=None):
        if client is None:
            try:
                import anthropic
            except ImportError as exc:
                raise RuntimeError("the model client needs the SDK: run `uv sync --extra model`") from exc
            client = anthropic.Anthropic()
        self._client = client
        self.model = model
        self.effort = effort

    def complete(self, *, system: str, user: str, schema: dict, max_tokens: int = 16000) -> ModelReply:
        response = self._client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            thinking={"type": "adaptive"},
            output_config={"effort": self.effort, "format": {"type": "json_schema", "schema": schema}},
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        usage = response.usage
        used = {
            "model": response.model,
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
        }
        if response.stop_reason == "refusal":
            return ModelReply("", refused=True, **used)
        text = "".join(block.text for block in response.content if block.type == "text")
        return ModelReply(text, **used)
