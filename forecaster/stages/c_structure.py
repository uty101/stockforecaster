"""Stage C, structure. Raise.

Builds the evidence store: one byte-identical corpus every lens reads from.
Deterministic, no model call.

A claim cannot be constructed without a source and a quote. That is enforced in
the constructor rather than checked afterwards, because a rule checked afterwards
is a rule something eventually skips.

Verification splits three ways and the distinction matters.

  prose       A human wrote sentences, a model quoted one, and the quote must be
              found in the text. This is where fabrication is possible and where
              string matching earns its keep.

  structured  The quote is the tagged fact itself, rendered by a source adapter
              from a typed response. There is no prose to match. Not a loophole:
              the value came from the adapter and never from a model.

  derived     A figure the pipeline computed. Deliberately in neither set, because
              it is only as good as its inputs and must be justified by the claims
              underneath it rather than by its own existence.

News is deliberately not evidence. We hold a snippet rather than the article, so
matching a quote against the snippet would be circular, and coverage published
days before a print is preview and positioning rather than fact. It stays on the
dossier, feeds perception only, and no lens can cite it.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from ..context import RunContext
from ..documents import Document
from .b5_extract import Extractions
from .b_acquire import Dossier

STAGE = "C"

PROSE = "prose"
STRUCTURED = "structured"
DERIVED = "derived"


class ProvenanceError(Exception):
    """A claim was built without the source or the quote that makes it a claim."""


@dataclass(frozen=True)
class Claim:
    citation_id: str
    kind: str
    label: str
    value: float | None
    units: str
    basis: str
    period_label: str
    period_end: str | None
    quote: str
    doc_id: str
    source: str

    def __post_init__(self) -> None:
        if not self.source:
            raise ProvenanceError(f"{self.citation_id}: a claim cannot exist without a source")
        if not self.quote:
            raise ProvenanceError(f"{self.citation_id}: a claim cannot exist without a quote")
        if self.kind not in (PROSE, STRUCTURED, DERIVED):
            raise ProvenanceError(f"{self.citation_id}: unknown verification kind {self.kind!r}")

    def to_json(self) -> dict[str, Any]:
        return {
            "citation_id": self.citation_id,
            "kind": self.kind,
            "label": self.label,
            "value": self.value,
            "units": self.units,
            "basis": self.basis,
            "period_label": self.period_label,
            "period_end": self.period_end,
            "quote": self.quote,
            "doc_id": self.doc_id,
            "source": self.source,
        }


@dataclass
class EvidenceStore:
    ticker: str
    claims: list[Claim] = field(default_factory=list)
    guidance: list[Claim] = field(default_factory=list)
    documents: dict[str, dict[str, Any]] = field(default_factory=dict)
    consensus: dict[str, Any] | None = None
    dropped: list[dict[str, Any]] = field(default_factory=list)
    _by_id: dict[str, Claim] = field(default_factory=dict, repr=False)

    def add(self, claim: Claim) -> None:
        self.claims.append(claim)
        self._by_id[claim.citation_id] = claim

    def add_guidance(self, claim: Claim) -> None:
        """Guidance is kept in its own list because the lenses read it as a
        separate section, but it must be in the same lookup index. A claim the
        corpus shows to a lens and the index does not know about will drop every
        lens that cites it, which is a failure in this stage wearing V1's
        clothing."""
        self.guidance.append(claim)
        self._by_id[claim.citation_id] = claim

    def get(self, citation_id: str) -> Claim | None:
        return self._by_id.get(citation_id)

    def has(self, citation_id: str) -> bool:
        return citation_id in self._by_id

    @property
    def citation_ids(self) -> list[str]:
        return list(self._by_id)

    def split(self) -> dict[str, int]:
        counts = {PROSE: 0, STRUCTURED: 0, DERIVED: 0}
        for claim in self.claims + self.guidance:
            counts[claim.kind] += 1
        return counts

    def to_json(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker,
            "claims": [claim.to_json() for claim in self.claims],
            "guidance": [claim.to_json() for claim in self.guidance],
            "documents": self.documents,
            "consensus": self.consensus,
            "dropped": self.dropped,
            "verification_split": self.split(),
            "counts": {
                "claims": len(self.claims),
                "guidance": len(self.guidance),
                "documents": len(self.documents),
                "dropped": len(self.dropped),
            },
        }


def run(
    ctx: RunContext,
    dossier: Dossier,
    extractions: Extractions,
    consensus: dict[str, Any] | None,
) -> EvidenceStore:
    started = time.monotonic()
    ctx.events.emit(STAGE, "stage_started", nodes=["U10"], ticker=ctx.ticker)

    store = EvidenceStore(ticker=ctx.ticker)
    for document in dossier.all_documents:
        store.documents[document.doc_id] = document.summary()

    counter = 0
    seen: set[tuple[Any, ...]] = set()

    for extraction in extractions.metrics:
        for item in extraction.payload.get("values", []):
            key = (
                _norm(item.get("metric_label")),
                _norm(item.get("period_label")),
                item.get("value"),
                _norm(item.get("basis")),
            )
            if key in seen:
                # The same figure appears in the press release and again in the
                # prepared commentary. Deduplicate on the value, not the wording.
                store.dropped.append(
                    {
                        "label": item.get("metric_label"),
                        "doc_id": extraction.document.doc_id,
                        "reason": "duplicate of a claim already in the store",
                    }
                )
                continue
            seen.add(key)
            counter += 1
            store.add(
                _claim(
                    ctx, counter, PROSE, item, extraction.document, source="corpus"
                )
            )

    for extraction in extractions.guidance:
        for item in extraction.payload.get("items", []):
            counter += 1
            store.add_guidance(
                Claim(
                    citation_id=_cite(ctx, counter),
                    kind=PROSE,
                    label=item.get("metric_label") or "",
                    value=_guidance_value(item),
                    units="as stated",
                    basis=item.get("basis") or "unstated",
                    period_label=item.get("period") or "",
                    period_end=None,
                    quote=item.get("quote") or "",
                    doc_id=extraction.document.doc_id,
                    source="corpus",
                )
            )

    if consensus:
        store.consensus = consensus
        for label, block, units in (
            ("Street consensus, EPS", consensus.get("eps") or {}, "per share"),
            ("Street consensus, revenue", consensus.get("revenue") or {}, "absolute"),
        ):
            if block.get("mean") is None:
                continue
            counter += 1
            store.add(
                Claim(
                    citation_id=_cite(ctx, counter),
                    kind=STRUCTURED,
                    label=label,
                    value=block["mean"],
                    units=units,
                    basis="street consensus",
                    period_label=consensus.get("requested_period") or "",
                    period_end=consensus.get("period_end"),
                    # The tagged fact is its own quote. No prose exists to match
                    # because no model was ever in this path.
                    quote=(
                        f"{label} {block['mean']} across {block.get('analysts')} analysts, "
                        f"published by {consensus.get('source')} on {consensus.get('computed_at')}"
                    ),
                    doc_id=f"{consensus.get('source')}:{consensus.get('symbol')}",
                    source=consensus.get("source") or "market",
                )
            )
    else:
        ctx.note(
            STAGE,
            "degrade",
            "no consensus reached the evidence store, so the forecast has no Street anchor and "
            "lambda cannot be applied; the own estimate will stand and must be labelled as such",
        )

    if not store.claims:
        raise ValueError(
            f"{ctx.ticker}: the evidence store is empty. Every lens would have nothing to cite, "
            "so the run cannot continue."
        )

    for record in extractions.dropped:
        store.dropped.append(record)

    ctx.events.emit(
        STAGE,
        "stage_finished",
        duration_s=round(time.monotonic() - started, 3),
        cost_usd=0.0,
        **store.to_json()["counts"],
        verification_split=store.split(),
    )
    return store


def _claim(
    ctx: RunContext, index: int, kind: str, item: dict[str, Any], document: Document, source: str
) -> Claim:
    return Claim(
        citation_id=_cite(ctx, index),
        kind=kind,
        label=item.get("metric_label") or "",
        value=item.get("value"),
        units=item.get("units_as_reported") or "",
        basis=item.get("basis") or "unstated",
        period_label=item.get("period_label") or "",
        period_end=item.get("period_end"),
        quote=item.get("quote") or "",
        doc_id=document.doc_id,
        source=source,
    )


def _cite(ctx: RunContext, index: int) -> str:
    return f"{ctx.ticker.replace(':', '')}-{index:03d}"


def _guidance_value(item: dict[str, Any]) -> float | None:
    if item.get("point") is not None:
        return item["point"]
    low, high = item.get("low"), item.get("high")
    if low is not None and high is not None:
        return (low + high) / 2
    return low if low is not None else high


def _norm(value: Any) -> str:
    return " ".join(str(value or "").lower().split())
