"""Stage B, acquire. Drop.

Takes the loaded sources and produces a dossier on disk keyed by ticker and
as-of date. Every acquirer gets a ranked target list and a budget in documents.
When one runs out, an event lists exactly what was skipped and that list is
carried into the output, because silent truncation reads downstream as complete
coverage and that is the most expensive quiet failure in the system.

Running from a dossier skips acquisition entirely, so bugs in this stage are
invisible in replay, and any change here must be run once against the corpus
cold before it is trusted.

One departure from the specification worth stating plainly: the dossier records
document identity and path rather than copying 102 MB of text into the run
directory. The corpus is local, frozen at 2026-08-14 and read-only, so a path is
byte-identical evidence in a way a network fetch never would be. If this ever
points at a live source the text must be copied instead.

What the specification asks for and this corpus does not contain: consensus,
peers, industry sizing, macro series, FX rates and price history. Those come
back empty with a named reason, and the reason travels to the Integrity sheet.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from ..context import RunContext
from ..documents import Document
from ..forms import (
    EARNINGS_CALL,
    EIGHT_K,
    TEN_K,
    TEN_Q,
    call_kind_of,
    call_segment_of,
    fiscal_tag_of,
    form_of,
)
from ..stages.a_sources import LoadedSources

STAGE = "B"


@dataclass
class CallSection:
    """One filed half of one earnings call."""

    document: Document
    segment: str | None


@dataclass
class EarningsCall:
    """One call, as an ordered position in a sequence rather than a loose item.

    What management stopped saying between the sixth call and the eighth is a
    fact, and it is invisible reading any single call. So the sequence carries
    its own ordering and dates and the transcript reader is handed all of it.
    """

    held_on: date
    fiscal_tag: str | None
    sections: list[CallSection] = field(default_factory=list)

    @property
    def documents(self) -> list[Document]:
        return [section.document for section in self.sections]


@dataclass
class Dossier:
    ticker: str
    company: str
    period: str
    as_of: date

    earnings_releases: list[Document] = field(default_factory=list)
    periodic_reports: list[Document] = field(default_factory=list)
    other_filings: list[Document] = field(default_factory=list)
    call_sequence: list[EarningsCall] = field(default_factory=list)
    other_calls: list[Document] = field(default_factory=list)
    slides: list[Document] = field(default_factory=list)

    absent: dict[str, str] = field(default_factory=dict)
    skipped: list[dict[str, Any]] = field(default_factory=list)

    @property
    def latest_earnings_release(self) -> Document | None:
        return self.earnings_releases[0] if self.earnings_releases else None

    @property
    def all_documents(self) -> list[Document]:
        seen: dict[str, Document] = {}
        for group in (
            self.earnings_releases,
            self.periodic_reports,
            self.other_filings,
            [doc for call in self.call_sequence for doc in call.documents],
            self.other_calls,
            self.slides,
        ):
            for document in group:
                seen.setdefault(document.doc_id, document)
        return list(seen.values())

    def to_json(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker,
            "company": self.company,
            "period": self.period,
            "as_of": self.as_of.isoformat(),
            "counts": {
                "earnings_releases": len(self.earnings_releases),
                "periodic_reports": len(self.periodic_reports),
                "other_filings": len(self.other_filings),
                "earnings_calls": len(self.call_sequence),
                "slides": len(self.slides),
            },
            "earnings_releases": [doc.summary() for doc in self.earnings_releases],
            "periodic_reports": [doc.summary() for doc in self.periodic_reports],
            "call_sequence": [
                {
                    "position": index,
                    "held_on": call.held_on.isoformat(),
                    "fiscal_tag": call.fiscal_tag,
                    "sections": [
                        {"segment": section.segment, **section.document.summary()}
                        for section in call.sections
                    ],
                }
                for index, call in enumerate(self.call_sequence)
            ],
            "slides": [doc.summary() for doc in self.slides],
            "absent": self.absent,
            "skipped": self.skipped,
            "paths": {doc.doc_id: str(doc.path) for doc in self.all_documents},
        }


# What the specification asks each acquirer for, in rank order, and what this
# corpus can answer. Everything unanswered is named rather than omitted.
ABSENT_REASONS = {
    "consensus": "no source in the chain publishes analyst consensus; lambda has no coverage inputs",
    "peers": "the corpus holds only the four target companies, so no peer has reported into this period",
    "industry_sizing": "no third-party industry data in the corpus",
    "macro": "no macro series in the corpus; FRED is out of the source chain for this run",
    "fx_rates": "no FX series in the corpus; FX exposure must come from filings text instead",
    "prices": "no price history in the corpus",
}


def run(ctx: RunContext, sources: LoadedSources) -> Dossier:
    started = time.monotonic()
    ctx.events.emit(STAGE, "stage_started", ticker=ctx.ticker)

    budgets = ctx.config.section("acquire_budgets")
    chain = sources.chain

    dossier = Dossier(
        ticker=ctx.ticker,
        company=ctx.target.company,
        period=ctx.target.period,
        as_of=ctx.as_of,
    )

    filings: list[Document] = chain.fetch("filings", ctx.ticker, required=True)
    transcripts: list[Document] = chain.fetch("transcripts", ctx.ticker, required=True)
    slides: list[Document] = chain.fetch("slides", ctx.ticker) or []

    _acquire_filings(ctx, dossier, filings, budgets)
    _acquire_calls(ctx, dossier, transcripts, budgets)

    slide_budget = budgets.get("slides", 12)
    dossier.slides = slides[:slide_budget]
    _record_skips(ctx, dossier, "slides", slides[slide_budget:])

    for name, reason in ABSENT_REASONS.items():
        dossier.absent[name] = reason
    ctx.note(
        STAGE,
        "degrade",
        "acquired nothing for " + ", ".join(sorted(ABSENT_REASONS))
        + "; each is named in the dossier with its reason and no default is substituted",
        absent=sorted(ABSENT_REASONS),
    )

    path = write_dossier(ctx, dossier)
    ctx.events.emit(
        STAGE,
        "stage_finished",
        duration_s=round(time.monotonic() - started, 3),
        cost_usd=0.0,
        dossier=str(path),
        **dossier.to_json()["counts"],
    )
    return dossier


def _acquire_filings(ctx: RunContext, dossier: Dossier, filings: list[Document], budgets: dict) -> None:
    """Rank by form, not by date. The date ranking is the trap."""
    releases = [doc for doc in filings if form_of(doc) == EIGHT_K]
    reports = [doc for doc in filings if form_of(doc) in (TEN_Q, TEN_K)]
    other = [doc for doc in filings if form_of(doc) not in (EIGHT_K, TEN_Q, TEN_K)]

    if not reports:
        # Hays files no 10-Q and no 10-K. Its statutory statements arrive inside
        # the H1 and H2 results announcements, which are tagged 8-Ks here. Named
        # substitution, not a silent one: the model built on it is reading a
        # half-yearly statement set and every stage downstream should know.
        reports = [doc for doc in releases if (fiscal_tag_of(doc) or "") in ("H1", "H2", "FY")]
        ctx.note(
            STAGE,
            "degrade",
            f"{ctx.ticker} files no 10-Q or 10-K; the statement history is taken from "
            f"{len(reports)} half-year and full-year results announcements instead, so it is "
            "half-yearly rather than quarterly",
            substituted_reports=len(reports),
        )

    release_budget = budgets.get("earnings_releases", 12)
    report_budget = budgets.get("history_periods", 24)
    other_budget = budgets.get("other_filings", 0)

    dossier.earnings_releases = releases[:release_budget]
    dossier.periodic_reports = reports[:report_budget]
    dossier.other_filings = other[:other_budget]

    _record_skips(ctx, dossier, "earnings_releases", releases[release_budget:])
    _record_skips(ctx, dossier, "periodic_reports", reports[report_budget:])
    _record_skips(ctx, dossier, "other_filings", other[other_budget:])

    if not dossier.earnings_releases:
        ctx.note(
            STAGE,
            "degrade",
            "no 8-K earnings release survived the form filter; guidance extraction at B5 has nothing to read",
            filings_seen=len(filings),
        )


def _acquire_calls(ctx: RunContext, dossier: Dossier, transcripts: list[Document], budgets: dict) -> None:
    earnings_sections = [doc for doc in transcripts if call_kind_of(doc) == EARNINGS_CALL]
    other = [doc for doc in transcripts if call_kind_of(doc) != EARNINGS_CALL]

    by_date: dict[date, EarningsCall] = {}
    for document in earnings_sections:
        call = by_date.get(document.published_at)
        if call is None:
            call = EarningsCall(held_on=document.published_at, fiscal_tag=fiscal_tag_of(document))
            by_date[document.published_at] = call
        call.sections.append(CallSection(document=document, segment=call_segment_of(document)))

    ordered = sorted(by_date.values(), key=lambda call: call.held_on, reverse=True)
    wanted = budgets["transcript_sequence_length"]

    # Oldest first inside the kept window: the sequence is read as a sequence,
    # and reversing it here means every consumer reads it in the order it happened.
    dossier.call_sequence = list(reversed(ordered[:wanted]))
    dossier.other_calls = other[: budgets.get("other_calls", 6)]

    dropped = ordered[wanted:]
    if dropped:
        _record_skips(
            ctx,
            dossier,
            "earnings_calls",
            [section.document for call in dropped for section in call.sections],
        )

    if len(dossier.call_sequence) < wanted:
        ctx.note(
            STAGE,
            "degrade",
            f"only {len(dossier.call_sequence)} earnings calls available against a requested "
            f"sequence of {wanted}; the transcript read covers a shorter window",
            available=len(dossier.call_sequence),
            requested=wanted,
        )


def _record_skips(ctx: RunContext, dossier: Dossier, acquirer: str, skipped: list[Document]) -> None:
    if not skipped:
        return
    record = {
        "acquirer": acquirer,
        "skipped": len(skipped),
        "reason": "budget exhausted",
        "doc_ids": [doc.doc_id for doc in skipped[:40]],
    }
    dossier.skipped.append(record)
    ctx.note(
        STAGE,
        "drop",
        f"{acquirer}: {len(skipped)} document(s) skipped on budget; downstream coverage is not complete",
        **record,
    )


def write_dossier(ctx: RunContext, dossier: Dossier) -> Path:
    path = ctx.run_dir / f"dossier-{ctx.ticker.replace(':', '_')}-{ctx.as_of.isoformat()}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(dossier.to_json(), handle, indent=2)
        handle.flush()
    temporary.replace(path)
    return path
