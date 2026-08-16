"""Configuration and the forecast target.

Two files feed this: config/forecaster.json, which is ours, and
challenge/companies.json, which the organisers supply and we never edit. The
metric labels, units and period label in the workbook come from theirs; if the
two ever disagree the run should fail loudly rather than park a forecast against
the wrong metric.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent

# The three shapes a target metric can take. The pipeline never treats a metric
# as "EPS or revenue": twelve of the numbers we owe are neither.
MONEY = "money"
PER_SHARE = "per_share"
PERCENT = "percent"


@dataclass(frozen=True)
class Metric:
    label: str
    units: str
    kind: str

    @property
    def is_percent(self) -> bool:
        return self.kind == PERCENT

    @property
    def is_subunit(self) -> bool:
        """True when the workbook wants pence rather than pounds."""
        return self.units.strip().lower() in SUBUNIT_UNITS


@dataclass(frozen=True)
class Target:
    """One company-period, carrying the three metrics we owe for it."""

    company: str
    ticker: str
    period: str
    output_file: str
    metrics: tuple[Metric, ...]

    @property
    def period_kind(self) -> str:
        """quarter | half | full_year, derived from the period label.

        Every prompt says "quarter". Hand a European full-year reporter a
        quarter-shaped prompt and all the lenses abstain honestly, nothing
        raises, and the run dies at V1 looking like a model problem when it is a
        vocabulary problem. So this is derived once, here, and passed everywhere.
        """
        label = self.period.upper()
        if "H1" in label or "H2" in label:
            return "half"
        if "Q" in label:
            return "quarter"
        return "full_year"

    @property
    def period_noun(self) -> str:
        return {"quarter": "quarter", "half": "half year", "full_year": "financial year"}[
            self.period_kind
        ]


# Explicit table first, because the units string decides what gets written into
# the workbook. "GBp" is pence per share, not a currency in millions, and a
# pattern rule that reads the trailing letter gets that exactly wrong.
UNIT_KINDS = {
    "%": PERCENT,
    "usdm": MONEY,
    "gbpm": MONEY,
    "eurm": MONEY,
    "usd / share": PER_SHARE,
    "gbp / share": PER_SHARE,
    "gbp": PER_SHARE,
    "gbx": PER_SHARE,
}

# Units whose numeric scale is not the reporting currency's main unit. Hays EPS
# is entered in pence: 6.2 means 6.2p, per SUBMISSION.md.
SUBUNIT_UNITS = {"gbp", "gbx"}


def classify_units(units: str) -> str:
    normalised = units.strip().lower()
    if normalised in UNIT_KINDS:
        return UNIT_KINDS[normalised]
    raise ValueError(
        f"unrecognised units {units!r}; add a rule rather than defaulting, "
        "because the units decide how the number is written into the workbook"
    )


@dataclass(frozen=True)
class Config:
    raw: dict[str, Any]
    path: Path

    @property
    def as_of(self) -> date:
        return date.fromisoformat(self.raw["as_of"])

    @property
    def corpus_frozen_at(self) -> date:
        return date.fromisoformat(self.raw["corpus_frozen_at"])

    @property
    def source_priority(self) -> list[str]:
        return list(self.raw["source_priority"])

    def source(self, name: str) -> dict[str, Any]:
        return self.raw["sources"][name]

    def section(self, name: str) -> dict[str, Any]:
        return self.raw[name]


def load_config(path: Path | None = None) -> Config:
    path = path or REPO_ROOT / "config" / "forecaster.json"
    return Config(raw=json.loads(path.read_text(encoding="utf-8")), path=path)


def load_targets(path: Path | None = None) -> list[Target]:
    path = path or REPO_ROOT / "challenge" / "companies.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    targets = []
    for entry in payload["companies"]:
        metrics = tuple(
            Metric(label=m["label"], units=m["units"], kind=classify_units(m["units"]))
            for m in entry["metrics"]
        )
        targets.append(
            Target(
                company=entry["company"],
                ticker=entry["ticker"],
                period=entry["period"],
                output_file=entry["outputFile"],
                metrics=metrics,
            )
        )
    return targets


def target_for(ticker: str, path: Path | None = None) -> Target:
    for target in load_targets(path):
        if target.ticker == ticker:
            return target
    raise KeyError(f"no target defined for ticker {ticker!r}")
