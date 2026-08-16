"""Stage V1, reconcile. Raise on total wipeout.

Deterministic, and that is the whole point. Nothing that can hallucinate decides
which hallucinations survive.

Two checks per lens. Every cited ID must already exist in the evidence store, and
every quote must be found in the claim it was attributed to. A citation ID that is
not in the store is not a warning: invented citations are exactly what the
provenance invariant exists to catch.

When a citation fails, the whole lens is dropped rather than the offending claim.
A lens that fabricated one citation has told you something about the rest of its
reasoning, and keeping the surviving claims would be keeping the parts of an
argument whose author you have just stopped trusting.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from ..context import RunContext
from ..documents import normalise
from .c_structure import EvidenceStore
from .e_analyse import LensView, LensViews

STAGE = "V1"


class EveryLensDropped(Exception):
    """A pipeline failure, not a forecast."""


@dataclass
class Reconciliation:
    surviving: list[LensView] = field(default_factory=list)
    dropped: list[dict[str, Any]] = field(default_factory=list)
    checked: int = 0
    matched: int = 0

    @property
    def verification_rate(self) -> float:
        return round(self.matched / self.checked, 4) if self.checked else 0.0

    def to_json(self) -> dict[str, Any]:
        return {
            "surviving": [view.lens for view in self.surviving],
            "dropped": self.dropped,
            "citations_checked": self.checked,
            "citations_matched": self.matched,
            "verification_rate": self.verification_rate,
        }


def run(ctx: RunContext, views: LensViews, evidence: EvidenceStore) -> Reconciliation:
    started = time.monotonic()
    ctx.events.emit(STAGE, "stage_started", nodes=["U21"], ticker=ctx.ticker)

    result = Reconciliation()

    for view in views.views:
        if view.deterministic:
            # Mechanical cites claims it was handed rather than claims it chose,
            # and it cannot invent one. It still passes through the ID check.
            failure = _first_failure(view, evidence, result, quotes=False)
        else:
            failure = _first_failure(view, evidence, result, quotes=True)

        if failure is None:
            result.surviving.append(view)
            continue

        view.dropped = True
        view.drop_reason = failure["reason"]
        result.dropped.append(failure)
        ctx.note(
            STAGE,
            "drop",
            f"{view.lens} dropped whole: {failure['reason']}",
            node=view.node_id,
            lens=view.lens,
            citation_id=failure.get("citation_id"),
            quote=failure.get("quote"),
        )

    if not result.surviving:
        raise EveryLensDropped(
            f"{ctx.ticker}: every lens was dropped at V1. That is a pipeline failure and not a "
            "forecast, so the run stops rather than emitting a number nothing supports."
        )

    ctx.events.emit(
        STAGE,
        "stage_finished",
        duration_s=round(time.monotonic() - started, 3),
        cost_usd=0.0,
        surviving=len(result.surviving),
        dropped=len(result.dropped),
        verification_rate=result.verification_rate,
    )
    return result


def _first_failure(
    view: LensView, evidence: EvidenceStore, result: Reconciliation, *, quotes: bool
) -> dict[str, Any] | None:
    for estimate in view.payload.get("estimates", []):
        citations = estimate.get("citations") or []
        if not citations and estimate.get("estimate") is not None:
            return {
                "lens": view.lens,
                "node_id": view.node_id,
                "reason": "produced an estimate with no citation at all; an uncited estimate is unfalsifiable",
            }
        for citation in citations:
            citation_id = (citation.get("citation_id") or "").strip()
            result.checked += 1

            claim = evidence.get(citation_id)
            if claim is None:
                return {
                    "lens": view.lens,
                    "node_id": view.node_id,
                    "citation_id": citation_id,
                    "reason": f"cited {citation_id!r}, which does not exist in the evidence store",
                }

            if quotes:
                quoted = normalise(citation.get("quote") or "")
                if not quoted or quoted not in normalise(claim.quote):
                    return {
                        "lens": view.lens,
                        "node_id": view.node_id,
                        "citation_id": citation_id,
                        "quote": (citation.get("quote") or "")[:200],
                        "reason": (
                            f"quoted text that is not in claim {citation_id}; the claim reads "
                            f"{claim.quote[:120]!r}"
                        ),
                    }
            result.matched += 1
    return None
