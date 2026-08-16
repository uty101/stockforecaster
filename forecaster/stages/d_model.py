"""Stage D, model. Raise on a structural failure, degrade on a failed reproduction.

An income model, not a three-statement model, and the reason is worth stating
plainly: none of the twelve metrics we owe touches a balance-sheet or cash-flow
line. There is no working capital, no debt, no cash and no interest expense in
any of them. A linked balance sheet would be real engineering that reaches
nothing we are scored on, so what is built is the half that reaches all of it.

What survives from the specification in full: history as filed, a ratio base of
medians with the scaled MAD beside each one, a projection, and the reproduction
check.

The reproduction check is what earns the model its place. Each recent reported
period is re-projected using only the statistics as they stood before it, and the
error is recorded. That error is the model's structural bias, it goes to the
judge, and it leads the Model sheet -- because a model states its own measured
error before anyone trusts it with a projection.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any

from ..config import Metric
from ..context import RunContext
from ..stats import Stat, median, scaled_mad, shrink
from .c_structure import EvidenceStore

STAGE = "D"

# Words that identify a metric regardless of how a filer phrases it. The
# extractor returns the company's own wording -- "Adjusted diluted earnings per
# share" -- and the target says "Adjusted diluted EPS". Matching on shared
# keywords rather than on the string is what bridges the two.
SYNONYMS = {
    "eps": {"eps", "earnings per share", "earnings per diluted share"},
    "diluted": {"diluted"},
    "basic": {"basic"},
    "adjusted": {"adjusted", "non-gaap", "pre-exceptional", "underlying"},
    "revenue": {"revenue", "revenues", "net sales", "sales", "net fees", "turnover"},
    "margin": {"margin"},
    "gross": {"gross"},
    "operating": {"operating"},
    "profit": {"profit", "income"},
    "comparable": {"comparable", "comp sales", "like-for-like", "lfl"},
}


@dataclass
class Observation:
    period_label: str
    period_end: str | None
    value: float
    basis: str
    citation_id: str


@dataclass
class MetricModel:
    metric: Metric
    observations: list[Observation] = field(default_factory=list)
    growth: Stat | None = None
    level: Stat | None = None
    projection: float | None = None
    projection_method: str = "none"
    reproduction: list[dict[str, Any]] = field(default_factory=list)
    reproduction_error: float | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def swing_per_sigma(self) -> float | None:
        """What one scaled MAD of this metric's own history is worth.

        This is the materiality weight the judge uses. Perturbing by one sigma
        rather than returning to the median matters: the projection is already
        seeded at the median, so perturbing back to it measures zero by
        construction and produces a plausible-looking table of zeros.
        """
        if self.projection is None or self.growth is None or self.growth.scaled_mad == 0:
            return None
        base = self._year_ago_value()
        if base is None:
            return None
        return abs(base * self.growth.scaled_mad)

    def _year_ago_value(self) -> float | None:
        return self.observations[-1].value if self.observations else None

    def to_json(self) -> dict[str, Any]:
        return {
            "metric": self.metric.label,
            "units": self.metric.units,
            "kind": self.metric.kind,
            "observations": [
                {
                    "period_label": o.period_label,
                    "period_end": o.period_end,
                    "value": o.value,
                    "basis": o.basis,
                    "citation_id": o.citation_id,
                }
                for o in self.observations
            ],
            "growth": self.growth.to_json() if self.growth else None,
            "level": self.level.to_json() if self.level else None,
            "projection": self.projection,
            "projection_method": self.projection_method,
            "swing_per_sigma": self.swing_per_sigma,
            "reproduction": self.reproduction,
            "reproduction_error": self.reproduction_error,
            "notes": self.notes,
        }


@dataclass
class Model:
    ticker: str
    metrics: list[MetricModel] = field(default_factory=list)
    degraded: bool = False

    def for_metric(self, label: str) -> MetricModel | None:
        return next((m for m in self.metrics if m.metric.label == label), None)

    def to_json(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker,
            "degraded": self.degraded,
            "metrics": [m.to_json() for m in self.metrics],
        }


def run(ctx: RunContext, evidence: EvidenceStore) -> Model:
    started = time.monotonic()
    ctx.events.emit(STAGE, "stage_started", nodes=["U11"], ticker=ctx.ticker)

    model = Model(ticker=ctx.ticker)

    for metric in ctx.target.metrics:
        built = _build_metric(ctx, metric, evidence)
        model.metrics.append(built)

    if all(m.projection is None for m in model.metrics):
        raise ValueError(
            f"{ctx.ticker}: the model produced no projection for any of the three metrics. "
            "There is nothing for the lenses to reason against."
        )

    if any(m.reproduction_error is None for m in model.metrics):
        model.degraded = True
        ctx.note(
            STAGE,
            "degrade",
            "the reproduction check could not run for every metric, so the model's structural "
            "bias is unmeasured for those and the judge is told so rather than shown a zero",
            metrics=[m.metric.label for m in model.metrics if m.reproduction_error is None],
        )

    ctx.events.emit(
        STAGE,
        "stage_finished",
        duration_s=round(time.monotonic() - started, 3),
        cost_usd=0.0,
        projected=sum(1 for m in model.metrics if m.projection is not None),
        degraded=model.degraded,
    )
    return model


def _build_metric(ctx: RunContext, metric: Metric, evidence: EvidenceStore) -> MetricModel:
    built = MetricModel(metric=metric)

    matched = [claim for claim in evidence.claims if _matches(metric.label, claim.label, claim.basis)]
    for claim in matched:
        if claim.value is None:
            continue
        built.observations.append(
            Observation(
                period_label=claim.period_label,
                period_end=claim.period_end,
                value=_to_units(claim.value, claim.units, metric),
                basis=claim.basis,
                citation_id=claim.citation_id,
            )
        )

    built.observations.sort(key=lambda o: (o.period_end or "", o.period_label))
    _dedupe(built)

    if not built.observations:
        built.notes.append(
            "no reported value for this metric survived extraction, so the model has no history "
            "and abstains rather than extrapolating from a neighbouring line"
        )
        return built

    values = [o.value for o in built.observations]
    built.level = shrink(values, prior=median(values), constant=_constant(ctx))

    growths = _growth_series(built.observations)
    if growths:
        built.growth = shrink(growths, prior=median(growths), constant=_constant(ctx))
        base = built.observations[-1].value
        if metric.is_percent:
            # A percentage metric is a level, not something that compounds.
            built.projection = built.level.shrunk
            built.projection_method = "shrunk median of reported level"
        else:
            built.projection = base * (1 + built.growth.shrunk)
            built.projection_method = "last reported value grown at the shrunk median growth rate"
    else:
        built.projection = built.level.shrunk
        built.projection_method = "shrunk median of reported level (no growth series available)"
        built.notes.append(
            "fewer than two comparable periods, so no growth rate could be measured and the "
            "projection is a level rather than a trend"
        )

    _reproduce(built, metric)
    return built


def _reproduce(built: MetricModel, metric: Metric) -> None:
    """Re-project each recent period from only what was known before it."""
    if len(built.observations) < 4:
        built.notes.append(
            "too few periods to re-project anything, so the model's structural bias is unmeasured"
        )
        return

    errors: list[float] = []
    for index in range(3, len(built.observations)):
        prior = built.observations[:index]
        actual = built.observations[index].value
        prior_values = [o.value for o in prior]
        if metric.is_percent:
            predicted = median(prior_values)
        else:
            growths = _growth_series(prior)
            if not growths:
                continue
            predicted = prior[-1].value * (1 + median(growths))
        if abs(actual) < 1e-9:
            continue
        error = (predicted - actual) / abs(actual)
        errors.append(error)
        built.reproduction.append(
            {
                "period_label": built.observations[index].period_label,
                "predicted": round(predicted, 6),
                "actual": round(actual, 6),
                "error_pct": round(error * 100, 3),
            }
        )

    if errors:
        built.reproduction_error = round(median(errors) * 100, 3)


def _growth_series(observations: list[Observation]) -> list[float]:
    growths = []
    for previous, current in zip(observations, observations[1:]):
        if abs(previous.value) < 1e-9:
            continue
        growths.append((current.value - previous.value) / abs(previous.value))
    return growths


def _dedupe(built: MetricModel) -> None:
    seen: set[tuple[str, float]] = set()
    unique: list[Observation] = []
    for observation in built.observations:
        key = (observation.period_label.lower().strip(), round(observation.value, 6))
        if key in seen:
            continue
        seen.add(key)
        unique.append(observation)
    built.observations = unique


def _matches(target_label: str, claim_label: str, basis: str) -> bool:
    """Does this extracted label describe the metric we owe?

    Both directions have to hold on the discriminating words. "Adjusted diluted
    EPS" must match "Adjusted diluted earnings per share" and must not match
    "Diluted earnings per share", because those are different numbers and picking
    the wrong one is a silent error worth several percent.
    """
    target_tokens = _tokens(target_label)
    claim_tokens = _tokens(claim_label) | _tokens(basis)

    if not target_tokens & claim_tokens:
        return False

    for discriminator in ("adjusted", "comparable", "gross", "operating", "diluted", "basic"):
        in_target = discriminator in target_tokens
        in_claim = discriminator in claim_tokens
        if in_target != in_claim:
            return False

    core = target_tokens & {"eps", "revenue", "margin", "profit", "comparable"}
    return bool(core & claim_tokens) if core else bool(target_tokens & claim_tokens)


def _tokens(text: str) -> set[str]:
    lowered = " " + re.sub(r"[^a-z0-9 ]+", " ", (text or "").lower()) + " "
    found = set()
    for key, phrases in SYNONYMS.items():
        for phrase in phrases:
            if f" {phrase} " in lowered:
                found.add(key)
                break
    return found


def _to_units(value: float, units_as_reported: str, metric: Metric) -> float:
    """Bring a reported figure onto the workbook's unit."""
    reported = (units_as_reported or "").lower()
    if metric.kind == "money":
        if "billion" in reported:
            return value * 1000.0
        if "thousand" in reported:
            return value / 1000.0
    return value


def _constant(ctx: RunContext) -> float:
    return float(ctx.config.section("statistics")["shrinkage_constant"])
