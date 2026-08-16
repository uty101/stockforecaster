"""Stage B5, extract. Drop.

Turns the prose of the results releases into typed, quoted claims. Two agents on
the cheap tier, kept separate because they read different document sets: the
metric extractor reads the whole run of releases to build history, the guidance
extractor reads only the most recent one. Merging them would load the larger set
twice.

Point the guidance extractor at the most recent results release and nothing else.
Not the periodic report, which is a hundred thousand characters of footnotes and
eats the budget for nothing. Not older releases, which guide for periods that
have already reported and hand you a stale range that looks current.

Every quote is string-matched back against the document it was attributed to.
Failures are dropped and each one emits an event naming what was lost. A range
assembled from two different sentences reads beautifully and is not what the
company said, and this is the only stage that can catch it.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from ..agents import schema
from ..agents.client import Client, plain
from ..agents.prompts import PromptStore
from ..agents.runner import fan_out
from ..context import RunContext
from ..documents import Document
from .b_acquire import Dossier

STAGE = "B5"

MAX_DOCUMENT_CHARS = 120_000

METRIC_VALUE = schema.obj(
    {
        "metric_label": schema.text("the metric, in the document's own words"),
        "value": schema.nullable("number", "the number as stated, or null if not stated"),
        "units_as_reported": schema.text("millions | billions | per share | percent | other"),
        "basis": schema.text("as-reported | adjusted | comparable | pre-exceptional | unstated"),
        "period_label": schema.text("the period this value covers, in the company's words"),
        "period_end": schema.nullable("string", "ISO date the period ended, or null"),
        "quote": schema.text("verbatim sentence from the document containing this value"),
    }
)

EXTRACT_METRICS = schema.obj(
    {
        "document_period": schema.text("the period this document reports on"),
        "values": schema.array_of(METRIC_VALUE, "every reported value found, comparatives included"),
    }
)

GUIDANCE_ITEM = schema.obj(
    {
        "metric_label": schema.text("what is being guided, in the company's words"),
        "period": schema.text("the period being guided"),
        "basis": schema.text("adjusted | GAAP | comparable | pre-exceptional | unstated"),
        "low": schema.nullable("number", "low end of a range, else null"),
        "high": schema.nullable("number", "high end of a range, else null"),
        "point": schema.nullable("number", "a point estimate, else null"),
        "withdrawn": {"type": "boolean", "description": "true when guidance was withdrawn"},
        "quote": schema.text("verbatim sentence containing the guidance"),
    }
)

EXTRACT_GUIDANCE = schema.obj(
    {"items": schema.array_of(GUIDANCE_ITEM, "forward guidance only; empty list is a real answer")}
)


@dataclass
class Extraction:
    kind: str
    document: Document
    payload: dict[str, Any]


@dataclass
class Extractions:
    metrics: list[Extraction] = field(default_factory=list)
    guidance: list[Extraction] = field(default_factory=list)
    dropped: list[dict[str, Any]] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return {
            "metrics": [
                {"doc_id": item.document.doc_id, **item.payload} for item in self.metrics
            ],
            "guidance": [
                {"doc_id": item.document.doc_id, **item.payload} for item in self.guidance
            ],
            "dropped": self.dropped,
            "counts": {
                "metric_documents": len(self.metrics),
                "metric_values": sum(len(item.payload.get("values", [])) for item in self.metrics),
                "guidance_items": sum(len(item.payload.get("items", [])) for item in self.guidance),
                "dropped": len(self.dropped),
            },
        }


def run(
    ctx: RunContext,
    dossier: Dossier,
    client: Client,
    prompts: PromptStore,
    *,
    history_documents: int = 12,
) -> Extractions:
    started = time.monotonic()
    ctx.events.emit(STAGE, "stage_started", nodes=["U1", "U4"], ticker=ctx.ticker)

    result = Extractions()
    releases = dossier.earnings_releases[:history_documents]

    if not releases:
        ctx.note(
            STAGE,
            "degrade",
            "no results release survived the form filter, so no metric history and no guidance "
            "could be extracted; every downstream estimate rests on the model alone",
        )
        return result

    metrics_prompt = prompts.get("extract_metrics")
    question = metrics_prompt.render(
        company=ctx.target.company,
        ticker=ctx.ticker,
        metrics_block=_metrics_block(ctx),
    )

    def extract_one(document: Document) -> Extraction:
        response = client.call(
            stage=STAGE,
            node="U1",
            agent="extract_metrics",
            tier_name="cheap",
            system_blocks=[plain(question)],
            user_text=_document_text(ctx, document),
            response_schema=EXTRACT_METRICS,
            max_tokens=8000,
            prompt_version=metrics_prompt.version,
        )
        return Extraction(kind="metrics", document=document, payload=response.value)

    for outcome in fan_out(
        releases,
        extract_one,
        events=ctx.events,
        stage=STAGE,
        label=lambda doc: doc.doc_id,
        max_workers=6,
    ):
        if isinstance(outcome, Exception):
            ctx.note(STAGE, "drop", f"metric extraction failed: {outcome}", agent="extract_metrics")
            result.dropped.append({"agent": "extract_metrics", "reason": str(outcome)})
            continue
        kept = _verified(ctx, result, outcome, "values")
        if kept.payload.get("values"):
            result.metrics.append(kept)

    latest = dossier.latest_earnings_release
    guidance_prompt = prompts.get("extract_guidance")
    try:
        response = client.call(
            stage=STAGE,
            node="U4",
            agent="extract_guidance",
            tier_name="cheap",
            system_blocks=[
                plain(guidance_prompt.render(company=ctx.target.company, ticker=ctx.ticker))
            ],
            user_text=_document_text(ctx, latest),
            response_schema=EXTRACT_GUIDANCE,
            max_tokens=6000,
            prompt_version=guidance_prompt.version,
        )
        extraction = _verified(
            ctx, result, Extraction("guidance", latest, response.value), "items"
        )
        result.guidance.append(extraction)
    except Exception as error:  # noqa: BLE001
        ctx.note(STAGE, "drop", f"guidance extraction failed: {error}", agent="extract_guidance")
        result.dropped.append({"agent": "extract_guidance", "reason": str(error)})

    ctx.events.emit(
        STAGE,
        "stage_finished",
        duration_s=round(time.monotonic() - started, 3),
        **result.to_json()["counts"],
    )
    return result


def _verified(
    ctx: RunContext, result: Extractions, extraction: Extraction, key: str
) -> Extraction:
    """Drop any item whose quote is not in the document it was attributed to."""
    kept: list[dict[str, Any]] = []
    for item in extraction.payload.get(key, []):
        quote = (item.get("quote") or "").strip()
        if quote and extraction.document.contains(quote):
            kept.append(item)
            continue
        record = {
            "agent": f"extract_{extraction.kind}",
            "doc_id": extraction.document.doc_id,
            "label": item.get("metric_label"),
            "quote": quote[:200],
            "reason": "quote not found in the document it was attributed to",
        }
        result.dropped.append(record)
        ctx.note(
            STAGE,
            "drop",
            f"{extraction.document.doc_id}: quote for {item.get('metric_label')!r} does not "
            "appear in the document; the value is discarded rather than trusted",
            **record,
        )
    extraction.payload[key] = kept
    return extraction


def _metrics_block(ctx: RunContext) -> str:
    lines = []
    for metric in ctx.target.metrics:
        lines.append(f"- **{metric.label}** (reported in {metric.units})")
    lines.append(
        "- Any headline revenue, earnings-per-share or margin figure the document states, "
        "even when it is not in the list above, because history for those is what the model runs on."
    )
    return "\n".join(lines)


def _document_text(ctx: RunContext, document: Document) -> str:
    text = document.text()
    if len(text) > MAX_DOCUMENT_CHARS:
        ctx.note(
            STAGE,
            "drop",
            f"{document.doc_id} is {len(text):,} characters and was truncated to "
            f"{MAX_DOCUMENT_CHARS:,}; anything after that point was not read",
            doc_id=document.doc_id,
            original_chars=len(text),
        )
        text = text[:MAX_DOCUMENT_CHARS]
    return (
        f"Document: {document.title}\n"
        f"Published: {document.published_at.isoformat()}\n"
        f"Type: {document.doc_type}\n\n"
        f"{text}"
    )
