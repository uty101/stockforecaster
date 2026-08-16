"""Stage H, position. Deterministic.

Positioning toward consensus is switched off for this run, by configuration
rather than by deletion.

The reasoning is the scoring rule. Accuracy here is our miss divided by the
Street's miss, capped at 5.0 and averaged over the twelve. A forecast shrunk hard
onto consensus inherits the Street's error and scores a ratio of about 1.0 by
construction -- safe, and unable to place. Submitting our own estimate is the only
way to score below 1.0, and it accepts a wider spread of outcomes in exchange.

This is the one place in the build where the specification's own instinct is
deliberately reversed, so it is written down rather than left to be inferred from
a lambda that happens to equal one.

What does not change: consensus is still acquired, still carried on its own bus,
still recorded with every regime condition it would have triggered, and still
rendered beside every forecast as the comparison and the baseline. The bus is
intact; only its connection to the submitted number is cut. Every regime condition
is still measured and reported, so the Risk sheet shows exactly how far the
positioning would have moved the number had it been applied.

The consensus bus is a first-class object here, not a diagram label. It is tapped
once where consensus enters the pipeline and carried untouched to lambda's second
input. No reasoning stage takes it as an argument, so there is no path by which a
lens or the judge could read from it or write to it.

The baseline sits in series on that bus and terminates there. It is consensus
times the shrunk company surprise, it is what the forecast is scored against, and
it is never an input to the forecast. A test asserts that.

Shrinking hard to consensus on a heavily covered name is the correct answer, not a
failure. Under this event's relative scoring there is no prize for being different
and a real penalty for being differently wrong.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from ..config import Metric
from ..context import RunContext
from ..stats import shrink
from .d_model import Model
from .g_judge import Judgement

STAGE = "H"

PRESETS = {"shrink": 0.20, "barbell": 0.55, "calibrated": 0.30}


@dataclass(frozen=True)
class ConsensusBus:
    """Tapped at U1, carried untouched, terminating at lambda's second input.

    Deliberately holds no method a reasoning stage could use. It is constructed in
    the orchestrator and handed only to this stage.
    """

    tapped_at_node: str
    raw: dict[str, Any] | None

    @property
    def available(self) -> bool:
        return bool(self.raw)

    def value_for(self, metric: Metric) -> float | None:
        if not self.raw:
            return None
        if metric.kind == "per_share":
            eps = self.raw.get("eps") or {}
            return eps.get("mean")
        if metric.kind == "money" and _is_revenue(metric.label):
            revenue = self.raw.get("revenue") or {}
            mean = revenue.get("mean")
            # Consensus revenue arrives in absolute currency; the workbook wants
            # millions. Converting in the wrong direction here is a 1,000,000x
            # error that would still look like a plausible number on the sheet.
            return mean / 1e6 if mean else None
        return None

    def conditions_input(self) -> dict[str, Any]:
        if not self.raw:
            return {}
        eps = self.raw.get("eps") or {}
        revisions = self.raw.get("revisions") or {}
        return {
            "analysts": eps.get("analysts"),
            "dispersion": eps.get("dispersion_range_pct"),
            "revisions_30d": (revisions.get("up_30d") or 0) + (revisions.get("down_30d") or 0),
        }


@dataclass
class Position:
    metric_label: str
    units: str
    own_estimate: float | None
    consensus: float | None
    baseline: float | None
    lam: float
    forecast: float | None
    preset: str
    conditions: list[dict[str, Any]] = field(default_factory=list)
    rationale: str = ""
    quantiles: dict[str, float] = field(default_factory=dict)
    beta_measured: bool = False

    def to_json(self) -> dict[str, Any]:
        return {
            "metric_label": self.metric_label,
            "units": self.units,
            "own_estimate": self.own_estimate,
            "consensus": self.consensus,
            "baseline": self.baseline,
            "lambda": self.lam,
            "forecast": self.forecast,
            "preset": self.preset,
            "conditions": self.conditions,
            "rationale": self.rationale,
            "quantiles": self.quantiles,
            "beta_measured": self.beta_measured,
        }


@dataclass
class Positions:
    bus_tapped_at: str
    consensus_available: bool
    positioning: str = "own_estimate_only"
    items: list[Position] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return {
            "consensus_bus": {
                "tapped_at_node": self.bus_tapped_at,
                "available": self.consensus_available,
                "positioning": self.positioning,
                "terminates_at": (
                    "comparison and baseline only. Positioning is switched off, so the bus "
                    "renders beside the forecast and does not feed it. The baseline terminates "
                    "on the bus and is never an input."
                ),
            },
            "positions": [item.to_json() for item in self.items],
        }


def run(
    ctx: RunContext,
    judgement: Judgement,
    model: Model,
    bus: ConsensusBus,
    comparability_fired: bool = False,
) -> Positions:
    started = time.monotonic()
    ctx.events.emit(STAGE, "stage_started", nodes=["U26"], ticker=ctx.ticker)

    config = ctx.config.section("lambda")
    preset_name = config.get("active_preset", "shrink")
    preset = float(config["presets"][preset_name])
    beta_measured = bool(config.get("beta_measured", False))
    positioning = config.get("positioning", "consensus_anchored")
    own_only = positioning == "own_estimate_only"

    result = Positions(
        bus_tapped_at=bus.tapped_at_node,
        consensus_available=bus.available,
        positioning=positioning,
    )

    for metric in ctx.target.metrics:
        judged = judgement.for_metric(metric.label)
        built = model.for_metric(metric.label)
        own_source = "judge"
        own = float(judged["p50"]) if judged else None
        if own is None and built is not None and built.projection is not None:
            # The judge produced nothing for this metric. Falling through to the
            # model keeps a number on the sheet, which matters because a blank
            # scores the maximum penalty -- but it is a different and weaker
            # thing than a judged forecast, so it is labelled as one.
            own = built.projection
            own_source = "model projection (the judge returned nothing for this metric)"
            ctx.note(
                STAGE,
                "degrade",
                f"{metric.label}: no judged distribution, so the forecast falls back to the "
                "deterministic model projection and is labelled as unjudged",
                metric=metric.label,
            )
        consensus = bus.value_for(metric)

        conditions: list[dict[str, Any]] = []
        lam = preset

        if own_only:
            # Measure every condition anyway. They are what the Risk sheet
            # renders, and reporting the positioning we chose not to apply is
            # more honest than reporting none at all.
            measured = _measure_conditions(bus, judgement, metric, comparability_fired)
            would_be = preset
            for condition in measured:
                if condition["multiplier"] is not None:
                    would_be *= condition["multiplier"]
            would_be = max(0.0, min(1.0, would_be))
            result.items.append(
                Position(
                    metric_label=metric.label,
                    units=metric.units,
                    own_estimate=own,
                    consensus=consensus,
                    baseline=_baseline(consensus, built),
                    lam=1.0,
                    forecast=own,
                    preset=preset_name,
                    conditions=measured
                    + [
                        {
                            "name": "positioning_disabled",
                            "input": positioning,
                            "multiplier": None,
                            "effect": (
                                f"lambda forced to 1.0; the conditions above were measured but not "
                                f"applied. Had positioning been on, lambda would have been "
                                f"{would_be:.3f}"
                            ),
                        }
                    ],
                    rationale=(
                        f"Own estimate from {own_source}, submitted unadjusted. Positioning toward "
                        f"consensus is switched off: our error is scored against the Street's, so a "
                        f"number shrunk onto consensus inherits the Street's error and cannot place. "
                        + (
                            f"Consensus for comparison is {consensus:.4g} and the baseline is "
                            f"{_baseline(consensus, built):.4g}; neither is an input here. "
                            if consensus is not None
                            else "No Street consensus exists for this metric in any case. "
                        )
                        + f"Had positioning been applied, lambda would have been {would_be:.3f}."
                    ),
                    quantiles=_quantiles(judged),
                    beta_measured=beta_measured,
                )
            )
            continue

        if consensus is None:
            position = Position(
                metric_label=metric.label,
                units=metric.units,
                own_estimate=own,
                consensus=None,
                baseline=None,
                lam=1.0,
                forecast=own,
                preset=preset_name,
                conditions=[
                    {
                        "name": "consensus_missing",
                        "input": None,
                        "multiplier": None,
                        "effect": "lambda not applied",
                    }
                ],
                rationale=(
                    f"Own estimate from {own_source}. "
                    f"No Street consensus exists for {metric.label}, so there is nothing to shrink "
                    f"toward and no lambda is applied against a zero. Our own estimate stands, and "
                    f"the forecast should be read as an unanchored estimate rather than a "
                    f"positioned one."
                ),
                quantiles=_quantiles(judged),
                beta_measured=beta_measured,
            )
            result.items.append(position)
            continue

        inputs = bus.conditions_input()
        analysts = inputs.get("analysts")
        dispersion = inputs.get("dispersion")
        revisions = inputs.get("revisions_30d")

        if analysts is not None and analysts <= 5:
            lam, conditions = _apply(lam, conditions, "thin_coverage", analysts, 1.8)
        elif analysts is not None and analysts >= 40:
            lam, conditions = _apply(lam, conditions, "heavy_coverage", analysts, 0.4)
        if dispersion is not None and dispersion > 0.15:
            lam, conditions = _apply(lam, conditions, "wide_dispersion", dispersion, 1.4)
        if revisions is not None and revisions == 0:
            lam, conditions = _apply(lam, conditions, "stale_revisions", revisions, 1.3)

        disagreement = judgement.disagreement.get(metric.label, {})
        coefficient = disagreement.get("coefficient") or 0.0
        if coefficient > 0.25:
            lam, conditions = _apply(
                lam, conditions, "internal_disagreement", coefficient, 0.25 / coefficient
            )
        if comparability_fired:
            lam, conditions = _apply(lam, conditions, "comparability_flag", True, 0.25)

        lam = max(0.0, min(1.0, lam))
        baseline = _baseline(consensus, built)
        forecast = consensus + lam * (own - consensus) if own is not None else consensus

        result.items.append(
            Position(
                metric_label=metric.label,
                units=metric.units,
                own_estimate=own,
                consensus=consensus,
                baseline=baseline,
                lam=round(lam, 4),
                forecast=forecast,
                preset=preset_name,
                conditions=conditions,
                rationale=f"Own estimate from {own_source}. " + _rationale(
                    metric, preset_name, preset, conditions, lam, consensus, own, forecast, beta_measured
                ),
                quantiles=_quantiles(judged),
                beta_measured=beta_measured,
            )
        )

    # One event per metric, carrying the conditions with the inputs that fired
    # them. These already reach results.json inside the Position, but that file
    # is written at stage I and a run that dies at H would take the whole record
    # of how lambda was decided with it.
    for item in result.items:
        ctx.events.emit(
            STAGE,
            "position",
            metric=item.metric_label,
            units=item.units,
            preset=preset_name,
            **{"lambda": item.lam},
            own_estimate=item.own_estimate,
            consensus=item.consensus,
            baseline=item.baseline,
            forecast=item.forecast,
            conditions=item.conditions,
        )

    ctx.events.emit(
        STAGE,
        "stage_finished",
        duration_s=round(time.monotonic() - started, 3),
        cost_usd=0.0,
        positioned=len(result.items),
        consensus_available=bus.available,
        positioning=positioning,
    )
    return result


def _measure_conditions(
    bus: "ConsensusBus", judgement: Judgement, metric: Metric, comparability_fired: bool
) -> list[dict[str, Any]]:
    """Every regime condition, with the input that fired it, measured but not applied."""
    measured: list[dict[str, Any]] = []
    inputs = bus.conditions_input()
    analysts = inputs.get("analysts")
    dispersion = inputs.get("dispersion")
    revisions = inputs.get("revisions_30d")

    if analysts is not None and analysts <= 5:
        measured.append({"name": "thin_coverage", "input": analysts, "multiplier": 1.8, "effect": "measured"})
    elif analysts is not None and analysts >= 40:
        measured.append({"name": "heavy_coverage", "input": analysts, "multiplier": 0.4, "effect": "measured"})
    if dispersion is not None and dispersion > 0.15:
        measured.append({"name": "wide_dispersion", "input": dispersion, "multiplier": 1.4, "effect": "measured"})
    if revisions == 0:
        measured.append({"name": "stale_revisions", "input": revisions, "multiplier": 1.3, "effect": "measured"})

    coefficient = (judgement.disagreement.get(metric.label) or {}).get("coefficient") or 0.0
    if coefficient > 0.25:
        measured.append(
            {"name": "internal_disagreement", "input": coefficient, "multiplier": round(0.25 / coefficient, 4), "effect": "measured"}
        )
    if comparability_fired:
        measured.append({"name": "comparability_flag", "input": True, "multiplier": 0.25, "effect": "measured"})
    if not measured:
        measured.append({"name": "no_condition_fired", "input": None, "multiplier": None, "effect": "measured"})
    return measured


def _apply(
    lam: float, conditions: list[dict[str, Any]], name: str, value: Any, multiplier: float
) -> tuple[float, list[dict[str, Any]]]:
    conditions.append(
        {"name": name, "input": value, "multiplier": round(multiplier, 4), "effect": "applied"}
    )
    return lam * multiplier, conditions


def _baseline(consensus: float | None, built: Any) -> float | None:
    """The bar to beat, computed from the company's own filings.

    The specification defines the baseline as consensus times the shrunk company
    surprise. With no consensus source in the chain that definition has nothing
    to stand on, so the baseline becomes the last reported period grown at the
    company's own shrunk median growth rate.

    It is still not a strawman. A naive continuation of a company's own recent
    trajectory is hard to improve on, and most of the value of this system is in
    beating that rather than in beating nothing. It terminates here exactly as
    the consensus baseline did: it is rendered beside the forecast and is never
    an input to it.
    """
    if built is None or not built.observations:
        return consensus
    values = [o.value for o in built.observations]
    growths = [
        (b - a) / abs(a) for a, b in zip(values, values[1:]) if abs(a) > 1e-9
    ]
    if not growths:
        return values[-1]
    stat = shrink(growths, prior=0.0, constant=6.0)
    return values[-1] * (1 + stat.shrunk)


def _quantiles(judged: dict[str, Any] | None) -> dict[str, float]:
    if not judged:
        return {}
    return {name: float(judged[name]) for name in ("p10", "p25", "p50", "p75", "p90")}


def _rationale(
    metric: Metric,
    preset_name: str,
    preset: float,
    conditions: list[dict[str, Any]],
    lam: float,
    consensus: float,
    own: float | None,
    forecast: float | None,
    beta_measured: bool,
) -> str:
    parts = [
        f"{metric.label}: preset {preset_name} sets the base lambda at {preset:.2f}"
        + ("" if beta_measured else ", which is asserted rather than fitted because the regression "
           "that would measure it needs a Street bar as it stood at past quarters, and no source "
           "we have publishes consensus history")
    ]
    if conditions:
        for condition in conditions:
            parts.append(
                f"{condition['name']} fired on an input of {condition['input']} and multiplied "
                f"lambda by {condition['multiplier']}"
            )
    else:
        parts.append("no regime condition fired")
    parts.append(f"lambda clamped to {lam:.3f}")
    if own is not None:
        parts.append(
            f"consensus {consensus:.4g}, our estimate {own:.4g}, so the forecast moves "
            f"{lam:.0%} of the way across to {forecast:.4g}"
        )
    return ". ".join(parts) + "."


def _is_revenue(label: str) -> bool:
    lowered = label.lower()
    return any(
        word in lowered for word in ("revenue", "net sales", "sales", "net fees", "turnover")
    )
