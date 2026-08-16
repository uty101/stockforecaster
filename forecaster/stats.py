"""House statistics. Medians and MAD, winsorize before any mean.

Scaled MAD is named for what it is everywhere it appears. A MAD standing in for a
standard deviation is scaled by 1.4826, and calling the variable `scaled_mad`
rather than `sigma` is the difference between a reader knowing that and assuming
it.

Any statistic on fewer than eight observations is shrunk before use, with the raw
value kept for display and labelled raw.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import Sequence

MAD_SCALE = 1.4826


@dataclass(frozen=True)
class Stat:
    """A statistic that carries how much evidence is underneath it."""

    raw: float
    shrunk: float
    observations: int
    scaled_mad: float
    prior: float
    shrinkage_weight: float

    @property
    def is_thin(self) -> bool:
        return self.observations < 8

    def to_json(self) -> dict[str, object]:
        return {
            "raw": round(self.raw, 6),
            "shrunk": round(self.shrunk, 6),
            "observations": self.observations,
            "scaled_mad": round(self.scaled_mad, 6),
            "prior": round(self.prior, 6),
            "shrinkage_weight": round(self.shrinkage_weight, 4),
            "thin": self.is_thin,
        }


def median(values: Sequence[float]) -> float:
    if not values:
        raise ValueError("median of nothing is not zero; it is a missing statistic")
    return float(statistics.median(values))


def scaled_mad(values: Sequence[float]) -> float:
    """Median absolute deviation, scaled to compare with a standard deviation."""
    if len(values) < 2:
        return 0.0
    centre = median(values)
    return float(statistics.median([abs(value - centre) for value in values]) * MAD_SCALE)


def winsorize(values: Sequence[float], lower_pct: float = 5, upper_pct: float = 95) -> list[float]:
    """Clip the tails before any mean. Never used before a median, which does not
    need it."""
    if len(values) < 3:
        return list(values)
    ordered = sorted(values)
    low = ordered[max(0, int(len(ordered) * lower_pct / 100) - 1)]
    high = ordered[min(len(ordered) - 1, int(len(ordered) * upper_pct / 100))]
    return [min(max(value, low), high) for value in values]


def shrink(
    values: Sequence[float], prior: float, constant: float = 6.0
) -> Stat:
    """Blend the company's own statistic with a prior, weighted by observation count.

    Weight on the company's own value is n / (n + k). Setting k low enough that
    the company's own history dominates is the same instinct as wanting a more
    differentiated forecast, and it is wrong for the same reason.
    """
    if not values:
        return Stat(raw=prior, shrunk=prior, observations=0, scaled_mad=0.0, prior=prior,
                    shrinkage_weight=0.0)
    raw = median(values)
    n = len(values)
    weight = n / (n + constant)
    return Stat(
        raw=raw,
        shrunk=weight * raw + (1 - weight) * prior,
        observations=n,
        scaled_mad=scaled_mad(values),
        prior=prior,
        shrinkage_weight=weight,
    )


def coefficient_of_variation(values: Sequence[float], fallback_scale: float | None = None) -> tuple[float, str]:
    """Scaled MAD over the absolute median, and which denominator was used.

    The absolute value matters: EPS can sit near zero or go negative, and an
    unguarded ratio explodes exactly when disagreement matters most.
    """
    if len(values) < 2:
        return 0.0, "insufficient_observations"
    centre = median(values)
    spread = scaled_mad(values)
    if abs(centre) > 1e-6:
        return spread / abs(centre), "absolute_median"
    if fallback_scale and abs(fallback_scale) > 1e-6:
        return spread / abs(fallback_scale), "historical_absolute_median"
    return 0.0, "median_too_close_to_zero"
