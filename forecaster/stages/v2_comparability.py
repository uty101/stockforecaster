"""Stage V2, comparability. One cheap-tier call.

Four questions about whether this period can be compared with the company's own
history: an acquisition or disposal inside the period, an accounting or policy
change, a 53rd week, and withdrawn guidance.

When one fires, the historical priors this system runs on stop applying. A system
that stays most confident exactly where its priors have broken is worse than one
that says it does not know, so a fired flag is carried into positioning and into
the output rather than noted and forgotten.

Absence of evidence is not evidence. The agent is told to answer false and say the
documents are silent, rather than reasoning about whether something is likely --
because a flag inferred from plausibility fires on the wrong quarters.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from ..agents import schema
from ..agents.client import Client, plain
from ..agents.prompts import PromptStore
from ..context import RunContext
from .b_acquire import Dossier

STAGE = "V2"

CHECKS = ("acquisition_or_disposal", "accounting_change", "extra_week", "withdrawn_guidance")

FLAG = schema.obj(
    {
        "check": schema.text("one of: " + ", ".join(CHECKS)),
        "fired": {"type": "boolean", "description": "true only when the evidence says so"},
        "evidence": schema.text("verbatim quote, or a statement that the documents are silent"),
        "reasoning": schema.text("one sentence"),
    }
)

COMPARABILITY = schema.obj({"flags": schema.array_of(FLAG, "one entry per check, four in total")})


@dataclass
class Comparability:
    flags: list[dict[str, Any]] = field(default_factory=list)
    degraded: bool = False
    degrade_reason: str = ""

    @property
    def fired(self) -> bool:
        return any(flag.get("fired") for flag in self.flags)

    @property
    def fired_names(self) -> list[str]:
        return [flag["check"] for flag in self.flags if flag.get("fired")]

    def to_json(self) -> dict[str, Any]:
        return {
            "flags": self.flags,
            "any_fired": self.fired,
            "fired": self.fired_names,
            "degraded": self.degraded,
            "degrade_reason": self.degrade_reason,
        }


def run(
    ctx: RunContext,
    dossier: Dossier,
    client: Client,
    prompts: PromptStore,
) -> Comparability:
    started = time.monotonic()
    ctx.events.emit(STAGE, "stage_started", nodes=["U24"], ticker=ctx.ticker)

    result = Comparability()
    prompt = prompts.get("comparability")

    documents = dossier.earnings_releases[:3] + dossier.periodic_reports[:1]
    if not documents:
        result.degraded = True
        result.degrade_reason = "no results release was available to check comparability against"
        ctx.note(STAGE, "degrade", result.degrade_reason, node="U24")
        return result

    body = "\n\n---\n\n".join(
        f"# {doc.title} ({doc.published_at.isoformat()})\n\n{doc.text()[:40_000]}"
        for doc in documents
    )

    try:
        response = client.call(
            stage=STAGE,
            node="U24",
            agent="comparability",
            tier_name="cheap",
            system_blocks=[
                plain(prompt.render(company=ctx.target.company, ticker=ctx.ticker, period=ctx.target.period))
            ],
            user_text=body,
            response_schema=COMPARABILITY,
            max_tokens=6000,
            prompt_version=prompt.version,
        )
        result.flags = response.value["flags"]
    except Exception as error:  # noqa: BLE001
        result.degraded = True
        result.degrade_reason = (
            f"the comparability check did not run ({error}); the period is treated as comparable "
            "by default, which is the optimistic assumption and is recorded as such"
        )
        ctx.note(STAGE, "degrade", result.degrade_reason, node="U24")
        return result

    if result.fired:
        ctx.note(
            STAGE,
            "degrade",
            f"comparability broke on {', '.join(result.fired_names)}; the historical priors this "
            "system runs on do not apply cleanly to this period and the forecast should be read "
            "with that in mind",
            node="U24",
            fired=result.fired_names,
        )

    ctx.events.emit(
        STAGE,
        "stage_finished",
        duration_s=round(time.monotonic() - started, 3),
        any_fired=result.fired,
        fired=result.fired_names,
    )
    return result
