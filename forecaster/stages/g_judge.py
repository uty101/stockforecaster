"""Stage G, judge. Exactly one deep-tier call. Degrade to a labelled fallback.

The highest-leverage decision in the system, which is why it is the only call on
the deep tier and why exactly one is allowed per run.

Five named float fields for the quantiles rather than a dictionary. A free-form
object asks the model to invent a key format and then populate it, and the common
failure is an empty object with the numbers written into the prose instead, which
validates fine and carries no forecast.

Monotonicity is enforced here rather than trusted. If the quantiles come back
crossed they are sorted and an event says so, because accepting them silently and
sorting them silently are both ways of hiding that it happened.

The fallback is the median of the surviving views and it labels itself degraded.
Never emit a degraded judgement that looks whole.
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
from ..stats import coefficient_of_variation, median
from .d_model import Model
from .e_analyse import LensViews
from .v1_reconcile import Reconciliation

STAGE = "G"

QUANTILES = ("p10", "p25", "p50", "p75", "p90")

JUDGED_METRIC = schema.obj(
    {
        "metric_label": schema.text("exactly one of the target metric labels"),
        "p10": schema.number("10th percentile, in the metric's own units"),
        "p25": schema.number("25th percentile"),
        "p50": schema.number("median — the point forecast"),
        "p75": schema.number("75th percentile"),
        "p90": schema.number("90th percentile"),
        "rationale": schema.text("which view carried the most weight and why"),
        "weighting": schema.text("what the weighting was based on"),
    }
)

JUDGEMENT = schema.obj(
    {
        "metrics": schema.array_of(JUDGED_METRIC, "one entry per target metric"),
        "missing_lenses_considered": schema.text("what the absent views would have covered"),
    }
)


@dataclass
class Judgement:
    metrics: list[dict[str, Any]] = field(default_factory=list)
    missing_lenses_considered: str = ""
    degraded: bool = False
    degrade_reason: str = ""
    disagreement: dict[str, Any] = field(default_factory=dict)
    crossed: list[str] = field(default_factory=list)

    def for_metric(self, label: str) -> dict[str, Any] | None:
        """Match tolerantly. The judge is given the metric label with its units
        beside it and will sometimes echo the pair back as one string, so an
        exact comparison silently finds nothing and every metric reads as
        unjudged."""
        wanted = _normalise_label(label)
        for record in self.metrics:
            if _normalise_label(record["metric_label"]) == wanted:
                return record
        for record in self.metrics:
            if wanted and wanted in _normalise_label(record["metric_label"]):
                return record
        return None

    def to_json(self) -> dict[str, Any]:
        return {
            "metrics": self.metrics,
            "missing_lenses_considered": self.missing_lenses_considered,
            "degraded": self.degraded,
            "degrade_reason": self.degrade_reason,
            "disagreement": self.disagreement,
            "crossed_quantiles_corrected": self.crossed,
        }


def run(
    ctx: RunContext,
    reconciliation: Reconciliation,
    views: LensViews,
    model: Model,
    client: Client,
    prompts: PromptStore,
    challenges: Any = None,
) -> Judgement:
    started = time.monotonic()
    ctx.events.emit(STAGE, "stage_started", nodes=["U23"], ticker=ctx.ticker)

    judgement = Judgement()
    judgement.disagreement = _disagreement(ctx, reconciliation)

    prompt = prompts.get("judge")
    try:
        response = client.call(
            stage=STAGE,
            node="U23",
            agent="judge",
            tier_name="deep",
            system_blocks=[
                plain(
                    prompt.render(
                        company=ctx.target.company,
                        ticker=ctx.ticker,
                        period=ctx.target.period,
                        period_noun=ctx.target.period_noun,
                        metric_focus="\n".join(
                            f"- {m.label} ({m.units})" for m in ctx.target.metrics
                        ),
                    )
                )
            ],
            user_text=_brief(ctx, reconciliation, views, model, challenges),
            response_schema=JUDGEMENT,
            max_tokens=16000,
            effort="high",
            prompt_version=prompt.version,
        )
        judgement.metrics = response.value["metrics"]
        judgement.missing_lenses_considered = response.value["missing_lenses_considered"]
    except Exception as error:  # noqa: BLE001
        judgement.degraded = True
        judgement.degrade_reason = f"the judge call failed ({error}); this is the labelled fallback"
        judgement.metrics = _fallback(ctx, reconciliation)
        judgement.missing_lenses_considered = "not assessed: the judge did not run"
        ctx.note(STAGE, "degrade", judgement.degrade_reason, node="U23")

    _enforce_monotonic(ctx, judgement)

    ctx.events.emit(
        STAGE,
        "stage_finished",
        duration_s=round(time.monotonic() - started, 3),
        degraded=judgement.degraded,
        metrics=len(judgement.metrics),
    )
    return judgement


def _brief(
    ctx: RunContext,
    reconciliation: Reconciliation,
    views: LensViews,
    model: Model,
    challenges: Any = None,
) -> str:
    lines: list[str] = []
    lines.append(f"## Surviving lens views ({len(reconciliation.surviving)})\n")
    for view in reconciliation.surviving:
        header = f"### {view.lens}{' (deterministic, no model)' if view.deterministic else ''}"
        if challenges is not None:
            surviving = challenges.weight_for(view.lens)
            header += (
                f"  — confidence after being argued against: {surviving:.2f}. Use this to discount "
                "the weight this view earns, never as a vote."
            )
        lines.append(header)
        for estimate in view.payload.get("estimates", []):
            value = estimate.get("estimate")
            lines.append(
                f"- {estimate.get('metric_label')}: "
                f"{'ABSTAINED' if value is None else value} "
                f"(confidence {estimate.get('confidence')})"
            )
            lines.append(f"  reasoning: {estimate.get('reasoning', '')[:600]}")
        lines.append(f"  would change my mind: {view.payload.get('what_would_change_my_mind', '')[:300]}\n")

    lines.append("\n## Lenses that produced no view, and why\n")
    if reconciliation.dropped:
        for record in reconciliation.dropped:
            lines.append(f"- {record['lens']}: DROPPED at reconciliation — {record['reason']}")
    for failure in views.failures:
        lines.append(f"- {failure['lens']}: failed to run — {failure['reason']}")
    if not reconciliation.dropped and not views.failures:
        lines.append("- none; every lens produced a view")

    lines.append("\n## What one sigma of each metric's own history is worth\n")
    for built in model.metrics:
        lines.append(
            f"- {built.metric.label}: swing {built.swing_per_sigma}, "
            f"model projection {built.projection}, "
            f"measured reproduction error {built.reproduction_error}%"
        )

    lines.append("\n## Disagreement between the surviving views\n")
    for label, record in _disagreement(ctx, reconciliation).items():
        lines.append(
            f"- {label}: coefficient of variation {record['coefficient']} "
            f"({record['denominator']}), {record['contributors']} views, "
            f"{record['abstentions']} abstained"
        )
    return "\n".join(lines)


def _normalise_label(label: str) -> str:
    text = re.sub(r"\(.*?\)", " ", str(label or "")).lower()
    return " ".join(re.sub(r"[^a-z0-9 ]+", " ", text).split())


def _disagreement(ctx: RunContext, reconciliation: Reconciliation) -> dict[str, Any]:
    """Abstaining lenses are excluded from the calculation and counted separately."""
    out: dict[str, Any] = {}
    for metric in ctx.target.metrics:
        values, abstentions = [], 0
        for view in reconciliation.surviving:
            estimate = view.estimate_for(metric.label)
            if estimate is None:
                continue
            if estimate.get("estimate") is None:
                abstentions += 1
            else:
                values.append(float(estimate["estimate"]))
        coefficient, denominator = coefficient_of_variation(values)
        out[metric.label] = {
            "coefficient": round(coefficient, 4),
            "denominator": denominator,
            "contributors": len(values),
            "abstentions": abstentions,
            "median": round(median(values), 6) if values else None,
        }
    return out


def _fallback(ctx: RunContext, reconciliation: Reconciliation) -> list[dict[str, Any]]:
    """The median of surviving views, labelled degraded on every metric."""
    metrics = []
    for metric in ctx.target.metrics:
        values = [
            float(view.estimate_for(metric.label)["estimate"])
            for view in reconciliation.surviving
            if view.estimate_for(metric.label)
            and view.estimate_for(metric.label).get("estimate") is not None
        ]
        if not values:
            continue
        centre = median(values)
        spread = max(abs(centre) * 0.08, 1e-6)
        metrics.append(
            {
                "metric_label": metric.label,
                "p10": centre - 1.28 * spread,
                "p25": centre - 0.67 * spread,
                "p50": centre,
                "p75": centre + 0.67 * spread,
                "p90": centre + 1.28 * spread,
                "rationale": (
                    "DEGRADED: the judge did not run. This is the median of the surviving lens "
                    "views with a nominal spread, not a weighted judgement, and it must not be "
                    "read as one."
                ),
                "weighting": "none applied — fallback",
            }
        )
    return metrics


def _enforce_monotonic(ctx: RunContext, judgement: Judgement) -> None:
    for record in judgement.metrics:
        values = [float(record[name]) for name in QUANTILES]
        if values != sorted(values):
            ordered = sorted(values)
            for name, value in zip(QUANTILES, ordered):
                record[name] = value
            judgement.crossed.append(record["metric_label"])
            ctx.note(
                STAGE,
                "degrade",
                f"{record['metric_label']}: the judge returned crossed quantiles; they have been "
                "sorted and the correction is recorded rather than applied silently",
                node="U23",
                metric=record["metric_label"],
                original=values,
            )
