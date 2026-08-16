"""Stage E, analyse. Nine lenses, blind to each other.

Each lens sees the shared evidence corpus, the income model, and its own question.
Nothing else. There is no parameter through which one lens's view could reach
another, and a change that would create one is rejected on that basis alone.

One call per lens covering all three metrics, rather than one call per lens per
metric. The evidence a lens needs is the same in all three cases, and splitting it
would triple the cost to re-read the same corpus.

The shared corpus is built once and placed first in every lens's system blocks, so
the cached prefix matches across the fan-out. The first lens completes before the
rest start, because a cold parallel fan-out means every lens calls before any
cache entry exists and all of them write while none reads.

Mechanical has no model call. It is arithmetic, it cannot hallucinate, and it is
the lens still standing when the API is unavailable -- which happened during this
build. It also has an explicit insufficient-inputs path, because a deterministic
lens always produces a number, and under materiality weighting a lens that cannot
decline would read as the most confident view in every run purely because it is
the only one that cannot.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any

from .. import nodes
from ..agents import schema
from ..agents.client import Client
from ..agents.prompts import PromptStore
from ..agents.runner import build_system_blocks, fan_out
from ..context import RunContext
from ..stats import median
from .c_structure import EvidenceStore
from .d_model import Model

STAGE = "E"


def normalise_label(label: str) -> str:
    """A lens is shown the metric label with its units beside it and will
    sometimes echo the pair back. An exact comparison then finds nothing and
    every estimate silently reads as an abstention."""
    text = re.sub(r"\(.*?\)", " ", str(label or "")).lower()
    return " ".join(re.sub(r"[^a-z0-9 ]+", " ", text).split())

CITATION = schema.obj(
    {
        "citation_id": schema.text("an ID that already exists in the evidence store"),
        "quote": schema.text("verbatim text from that claim"),
    }
)

ESTIMATE = schema.obj(
    {
        "metric_label": schema.text("exactly one of the target metric labels"),
        "estimate": schema.nullable("number", "your estimate in the metric's own units, or null to abstain"),
        "confidence": schema.number("0 to 1, what the evidence supports"),
        "reasoning": schema.text("how you got there, or why you abstained"),
        "citations": schema.array_of(CITATION, "at least one, even when abstaining"),
    }
)

LENS_VIEW = schema.obj(
    {
        "estimates": schema.array_of(ESTIMATE, "one entry per target metric"),
        "what_would_change_my_mind": schema.text("the observation that would move your view most"),
    }
)


@dataclass
class LensView:
    node_id: str
    lens: str
    payload: dict[str, Any]
    deterministic: bool = False
    dropped: bool = False
    drop_reason: str = ""

    def estimate_for(self, label: str) -> dict[str, Any] | None:
        wanted = normalise_label(label)
        for item in self.payload.get("estimates", []):
            if normalise_label(item.get("metric_label", "")) == wanted:
                return item
        for item in self.payload.get("estimates", []):
            if wanted and wanted in normalise_label(item.get("metric_label", "")):
                return item
        return None

    def to_json(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "lens": self.lens,
            "deterministic": self.deterministic,
            "dropped": self.dropped,
            "drop_reason": self.drop_reason,
            **self.payload,
        }


@dataclass
class LensViews:
    views: list[LensView] = field(default_factory=list)
    failures: list[dict[str, str]] = field(default_factory=list)

    @property
    def surviving(self) -> list[LensView]:
        return [view for view in self.views if not view.dropped]

    def to_json(self) -> dict[str, Any]:
        return {
            "views": [view.to_json() for view in self.views],
            "failures": self.failures,
            "counts": {
                "ran": len(self.views),
                "failed": len(self.failures),
            },
        }


def run(
    ctx: RunContext,
    evidence: EvidenceStore,
    model: Model,
    client: Client,
    prompts: PromptStore,
) -> LensViews:
    started = time.monotonic()
    lens_nodes = [nodes.node(node_id) for node_id in nodes.LENS_NODES]
    ctx.events.emit(STAGE, "stage_started", nodes=[n.id for n in lens_nodes], ticker=ctx.ticker)

    result = LensViews()
    result.views.append(_mechanical(ctx, model))

    corpus = build_corpus(ctx, evidence, model)
    model_nodes = [n for n in lens_nodes if n.tier != nodes.NONE_TIER]

    def run_lens(node: nodes.Node) -> LensView:
        prompt = prompts.get(node.agent)
        question = prompt.render(
            company=ctx.target.company,
            ticker=ctx.ticker,
            period=ctx.target.period,
            period_noun=ctx.target.period_noun,
            metric_focus=_metric_focus(ctx),
        )
        response = client.call(
            stage=STAGE,
            node=node.id,
            agent=node.agent,
            tier_name=node.tier,
            system_blocks=build_system_blocks(corpus, question, client.router[node.tier]),
            user_text=(
                f"Produce one estimate for each of the three metrics for "
                f"{ctx.target.company}, {ctx.target.period}."
            ),
            response_schema=LENS_VIEW,
            max_tokens=20000,
            effort="medium",
            prompt_version=prompt.version,
        )
        return LensView(node_id=node.id, lens=node.agent, payload=response.value)

    outcomes = fan_out(
        model_nodes,
        run_lens,
        events=ctx.events,
        stage=STAGE,
        label=lambda node: node.agent,
        max_workers=8,
    )

    for node, outcome in zip(model_nodes, outcomes):
        if isinstance(outcome, Exception):
            result.failures.append({"lens": node.agent, "node_id": node.id, "reason": str(outcome)})
            ctx.note(
                STAGE,
                "drop",
                f"{node.agent} failed and produced no view: {outcome}",
                node=node.id,
                lens=node.agent,
            )
            continue
        result.views.append(outcome)

    ctx.events.emit(
        STAGE,
        "stage_finished",
        duration_s=round(time.monotonic() - started, 3),
        **result.to_json()["counts"],
    )
    return result


def build_corpus(ctx: RunContext, evidence: EvidenceStore, model: Model) -> str:
    """The shared prefix every lens reads. Built once, placed first, identical
    across the fan-out so the cache can match it."""
    lines: list[str] = []
    lines.append(f"# Evidence corpus — {ctx.target.company} ({ctx.ticker}), {ctx.target.period}")
    lines.append(f"Lock date: {ctx.as_of.isoformat()}. Nothing here was published after it.\n")

    lines.append("## Metrics we owe\n")
    for metric in ctx.target.metrics:
        lines.append(f"- {metric.label} — reported in {metric.units}")

    if evidence.consensus:
        c = evidence.consensus
        eps, revenue = c.get("eps") or {}, c.get("revenue") or {}
        lines.append("\n## Street consensus (structured; not a quote from any document)\n")
        lines.append(
            f"- EPS mean {eps.get('mean')}, range {eps.get('low')}–{eps.get('high')}, "
            f"{eps.get('analysts')} analysts, dispersion {eps.get('dispersion_range_pct')}, "
            f"year ago {eps.get('year_ago')}"
        )
        lines.append(
            f"- Revenue mean {revenue.get('mean')}, {revenue.get('analysts')} analysts, "
            f"year ago {revenue.get('year_ago')}"
        )
        lines.append(f"- Revisions last 30 days: {c.get('revisions')}")
    else:
        lines.append("\n## Street consensus\n\nNone available for this company from any source.")

    lines.append("\n## Guidance the company has given\n")
    if evidence.guidance:
        for claim in evidence.guidance:
            lines.append(
                f"- [{claim.citation_id}] {claim.label} for {claim.period_label} "
                f"({claim.basis}): {claim.value} — \"{claim.quote[:260]}\""
            )
    else:
        lines.append("None extracted.")

    lines.append("\n## Reported claims\n")
    for claim in evidence.claims:
        if claim.kind == "structured":
            continue
        lines.append(
            f"- [{claim.citation_id}] {claim.label} ({claim.basis}) {claim.period_label}: "
            f"{claim.value} {claim.units} — \"{claim.quote[:240]}\""
        )

    lines.append("\n## Income model\n")
    for built in model.metrics:
        lines.append(
            f"- {built.metric.label}: projection {built.projection} "
            f"({built.projection_method}); {len(built.observations)} observations; "
            f"reproduction error {built.reproduction_error}%"
        )
        if built.notes:
            for note in built.notes:
                lines.append(f"  - note: {note}")

    return "\n".join(lines)


def _mechanical(ctx: RunContext, model: Model) -> LensView:
    """U12. Arithmetic only. Declines when its inputs are missing."""
    estimates = []
    for built in model.metrics:
        if built.projection is None or not built.observations:
            estimates.append(
                {
                    "metric_label": built.metric.label,
                    "estimate": None,
                    "confidence": 0.0,
                    "reasoning": (
                        "insufficient inputs: no reported history survived extraction for this "
                        "metric, so there is no arithmetic to do. A deterministic lens that "
                        "always answers would read as the most confident view in the run."
                    ),
                    "citations": [],
                }
            )
            continue

        values = [o.value for o in built.observations]
        estimates.append(
            {
                "metric_label": built.metric.label,
                "estimate": built.projection,
                "confidence": 0.35 if built.reproduction_error is None else 0.5,
                "reasoning": (
                    f"{built.projection_method}, from {len(values)} reported periods "
                    f"(median {median(values):.4g}). No judgement applied and no model call made."
                ),
                "citations": [
                    {"citation_id": o.citation_id, "quote": f"{o.period_label}: {o.value}"}
                    for o in built.observations[-3:]
                ],
            }
        )

    ctx.events.emit(
        STAGE,
        "agent_finished",
        node="U12",
        agent="lens_mechanical",
        tier="none",
        model="none",
        input_tokens=0,
        output_tokens=0,
        cache_write_tokens=0,
        cache_read_tokens=0,
        cost_usd=0.0,
        duration_s=0.0,
    )
    return LensView(
        node_id="U12",
        lens="lens_mechanical",
        payload={
            "estimates": estimates,
            "what_would_change_my_mind": "a reported period that changes the historical series",
        },
        deterministic=True,
    )


def _metric_focus(ctx: RunContext) -> str:
    return ", ".join(f"{m.label} ({m.units})" for m in ctx.target.metrics)
