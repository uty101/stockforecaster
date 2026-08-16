"""The narrative agent, at OUT. One mid-tier call.

It runs after the judge has produced a distribution and after positioning has
settled the number, so every figure it could cite already exists. This stage
argues the case; it does not compute it.

Deliberately mid rather than deep. Prose written from a settled answer is not
where the leverage is, and spending the expensive tier on it would take that call
away from the judge, which is the decision that actually moves the number.

The hard rule is that it may not introduce a figure absent from the results file.
That is enforced afterwards rather than trusted: every number in the returned
prose is pulled out and checked against the set of numbers the pipeline already
produced, and prose carrying an invented figure is rejected rather than printed.
Text that invents a number is worse than text that omits one, because it reads
exactly as authoritative.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any

from ..agents import schema
from ..agents.client import Client, plain
from ..agents.prompts import PromptStore
from ..context import RunContext

STAGE = "OUT"

NARRATIVE = schema.obj(
    {
        "headline": schema.text("one sentence: the number and what it rests on"),
        "case": schema.text("one paragraph making the case"),
        "strongest_objection": schema.text("the real reason it might be wrong"),
        "what_to_watch": schema.text("the line that would tell a reader soonest whether it was right"),
    }
)

NUMBER = re.compile(r"-?\d[\d,]*\.?\d*")


@dataclass
class Narrative:
    text: dict[str, str] = field(default_factory=dict)
    rejected: list[dict[str, Any]] = field(default_factory=list)
    degraded: bool = False
    degrade_reason: str = ""

    def to_json(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "rejected_for_inventing_a_figure": self.rejected,
            "degraded": self.degraded,
            "degrade_reason": self.degrade_reason,
        }


def run(
    ctx: RunContext,
    positions: Any,
    judgement: Any,
    reconciliation: Any,
    client: Client,
    prompts: PromptStore,
) -> Narrative:
    started = time.monotonic()
    ctx.events.emit(STAGE, "narrative_started", node="OUT", ticker=ctx.ticker)

    result = Narrative()
    allowed = _allowed_numbers(positions, judgement)
    prompt = prompts.get("narrative")

    try:
        response = client.call(
            stage=STAGE,
            node="OUT",
            agent="narrative",
            tier_name="mid",
            system_blocks=[
                plain(prompt.render(company=ctx.target.company, ticker=ctx.ticker, period=ctx.target.period))
            ],
            user_text=_material(ctx, positions, judgement, reconciliation),
            response_schema=NARRATIVE,
            max_tokens=4000,
            effort="medium",
            prompt_version=prompt.version,
        )
    except Exception as error:  # noqa: BLE001
        result.degraded = True
        result.degrade_reason = f"the narrative agent did not run ({error}); no written case exists"
        ctx.note(STAGE, "degrade", result.degrade_reason, node="OUT")
        return result

    for field_name, text in response.value.items():
        invented = _invented(text, allowed)
        if invented:
            result.rejected.append({"field": field_name, "figures": invented, "text": text})
            ctx.note(
                STAGE,
                "drop",
                f"narrative {field_name} introduced {', '.join(invented)}, which is not in the "
                "results file; the passage is discarded rather than printed",
                node="OUT",
                figures=invented,
            )
            continue
        result.text[field_name] = text

    if result.rejected:
        result.degraded = True
        result.degrade_reason = (
            f"{len(result.rejected)} passage(s) were discarded for citing a figure the pipeline "
            "never produced"
        )

    ctx.events.emit(
        STAGE,
        "narrative_finished",
        node="OUT",
        duration_s=round(time.monotonic() - started, 3),
        kept=len(result.text),
        rejected=len(result.rejected),
    )
    return result


def _allowed_numbers(positions: Any, judgement: Any) -> set[str]:
    """Every figure the pipeline actually produced, in the forms prose would use."""
    allowed: set[str] = set()

    def add(value: Any) -> None:
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            return
        for rendered in (
            f"{value:.0f}", f"{value:.1f}", f"{value:.2f}", f"{value:.3f}",
            f"{value:,.0f}", f"{value:,.1f}", f"{value:,.2f}", str(value),
        ):
            allowed.add(rendered.replace(",", ""))

    for item in positions.items:
        for key in ("forecast", "own_estimate", "consensus", "baseline", "lam"):
            add(getattr(item, key, None))
        for value in (item.quantiles or {}).values():
            add(value)
    for record in judgement.metrics:
        for key in ("p10", "p25", "p50", "p75", "p90"):
            add(record.get(key))
    # Small integers are ordinary prose ("three lenses", "one of four") rather
    # than claims about the company, so they are not treated as figures.
    for n in range(0, 101):
        allowed.add(str(n))
    return allowed


def _invented(text: str, allowed: set[str]) -> list[str]:
    found = []
    for match in NUMBER.findall(text or ""):
        cleaned = match.replace(",", "").rstrip(".")
        if not cleaned or cleaned in allowed:
            continue
        # Tolerate a trailing zero difference: 4.72 and 4.720 are the same figure.
        if cleaned.rstrip("0").rstrip(".") in {a.rstrip("0").rstrip(".") for a in allowed}:
            continue
        found.append(match)
    return found


def _material(ctx: RunContext, positions: Any, judgement: Any, reconciliation: Any) -> str:
    lines = [f"# {ctx.target.company} ({ctx.ticker}), {ctx.target.period}", ""]
    for item in positions.items:
        lines.append(f"## {item.metric_label} — {item.forecast} {item.units}")
        lines.append(item.rationale)
        if item.quantiles:
            lines.append("quantiles: " + ", ".join(f"{k} {v}" for k, v in item.quantiles.items()))
        lines.append("")
    lines.append("## What the judge said")
    for record in judgement.metrics:
        lines.append(f"- {record['metric_label']}: {record.get('rationale', '')}")
    lines.append("")
    lines.append("## Lenses that were dropped or abstained")
    for record in reconciliation.dropped:
        lines.append(f"- {record['lens']} dropped: {record['reason']}")
    if not reconciliation.dropped:
        lines.append("- none were dropped")
    return "\n".join(lines)
