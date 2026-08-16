"""The cost ledger and the hard ceiling.

The ceiling raises *before* the call that would breach it, never after, so the
kill switch is a refusal rather than an apology. Part 11 asserts this on call
count, not on the ledger total, because a ledger checked after the fact is not a
ceiling.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class CostCeilingExceeded(Exception):
    """Raised before a model call that would take the run past its ceiling."""


@dataclass
class CallRecord:
    stage: str
    agent: str
    tier: str
    model: str
    input_tokens: int
    output_tokens: int
    cache_write_tokens: int
    cache_read_tokens: int
    usd: float
    cached: bool
    duration_s: float


# USD per million tokens. Cache writes bill at 1.25x input, cache reads at 0.1x.
PRICES: dict[str, dict[str, float]] = {
    "claude-haiku-4-5": {"input": 1.00, "output": 5.00},
    "claude-sonnet-5": {"input": 3.00, "output": 15.00},
    "claude-opus-5": {"input": 15.00, "output": 75.00},
}


def price_for(model: str) -> dict[str, float]:
    """Match on prefix so dated snapshot IDs resolve to their family."""
    for prefix, prices in PRICES.items():
        if model.startswith(prefix):
            return prices
    raise KeyError(f"no price table entry for model {model!r}; add one rather than guessing")


def call_cost_usd(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_write_tokens: int = 0,
    cache_read_tokens: int = 0,
) -> float:
    prices = price_for(model)
    million = 1_000_000
    return (
        input_tokens * prices["input"] / million
        + output_tokens * prices["output"] / million
        + cache_write_tokens * prices["input"] * 1.25 / million
        + cache_read_tokens * prices["input"] * 0.10 / million
    )


@dataclass
class CostLedger:
    ceiling_usd: float
    calls: list[CallRecord] = field(default_factory=list)
    expensive_calls: int = 0

    @property
    def spent_usd(self) -> float:
        return sum(call.usd for call in self.calls)

    def check_before_call(self, estimated_usd: float) -> None:
        if self.spent_usd + estimated_usd > self.ceiling_usd:
            raise CostCeilingExceeded(
                f"call estimated at ${estimated_usd:.4f} would take the run to "
                f"${self.spent_usd + estimated_usd:.4f} against a ceiling of ${self.ceiling_usd:.2f}"
            )

    def record(self, call: CallRecord) -> CallRecord:
        self.calls.append(call)
        if call.tier == "expensive" and not call.cached:
            self.expensive_calls += 1
        return call

    def per_stage(self) -> dict[str, dict[str, Any]]:
        totals: dict[str, dict[str, Any]] = {}
        for call in self.calls:
            bucket = totals.setdefault(
                call.stage,
                {
                    "calls": 0,
                    "usd": 0.0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "cache_write_tokens": 0,
                    "cache_read_tokens": 0,
                },
            )
            bucket["calls"] += 1
            bucket["usd"] += call.usd
            bucket["input_tokens"] += call.input_tokens
            bucket["output_tokens"] += call.output_tokens
            bucket["cache_write_tokens"] += call.cache_write_tokens
            bucket["cache_read_tokens"] += call.cache_read_tokens
        return totals
