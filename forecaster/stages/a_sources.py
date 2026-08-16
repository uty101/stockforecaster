"""Stage A, sources. Raise.

Loads the adapters by priority from config and proves, before anything else
runs, that the chain can answer everything the pipeline requires for this
ticker. Deterministic, no model calls.

It raises only when every source in the chain returns nothing for something the
pipeline requires, and the error names the missing method. A source that cannot
answer an optional method is not a failure: the fall-through event is the
record, and for this event those events are the honest statement that we hold no
analyst consensus, no prices and no macro series.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from ..context import RunContext
from ..sources.loader import SourceChain, build_chain
from ..sources.protocol import OPTIONAL_METHODS, REQUIRED_METHODS

STAGE = "A"


@dataclass
class LoadedSources:
    chain: SourceChain
    available: dict[str, bool] = field(default_factory=dict)

    @property
    def names(self) -> list[str]:
        return self.chain.names

    def summary(self) -> dict[str, Any]:
        return {
            "priority": self.names,
            "required": {method: self.available.get(method, False) for method in REQUIRED_METHODS},
            "optional": {method: self.available.get(method, False) for method in OPTIONAL_METHODS},
        }


def run(ctx: RunContext) -> LoadedSources:
    started = time.monotonic()
    ctx.events.emit(STAGE, "stage_started", ticker=ctx.ticker, as_of=ctx.as_of.isoformat())

    chain = build_chain(ctx.config, ctx.events)
    loaded = LoadedSources(chain=chain)

    for method in REQUIRED_METHODS:
        value = chain.fetch(method, ctx.ticker, required=True)
        loaded.available[method] = bool(value)

    for method in OPTIONAL_METHODS:
        if method == "consensus":
            value = chain.fetch(method, ctx.ticker, ctx.target.period)
        elif method == "macro":
            value = chain.fetch(method, "fx_gbp_usd")
        else:
            value = chain.fetch(method, ctx.ticker)
        loaded.available[method] = value is not None

    missing_optional = [m for m in OPTIONAL_METHODS if not loaded.available[m]]
    if missing_optional:
        ctx.note(
            STAGE,
            "degrade",
            "no source answers " + ", ".join(missing_optional)
            + "; every stage that would have used them says so rather than substituting a value",
            methods=missing_optional,
        )

    ctx.events.emit(
        STAGE,
        "stage_finished",
        duration_s=round(time.monotonic() - started, 3),
        cost_usd=0.0,
        sources=loaded.names,
        available=loaded.available,
    )
    return loaded
