"""U7, U8 and U9: the three scanners that read across documents rather than into one.

Each answers something no single document contains.

U8 reads the eight earnings calls as a sequence. Its job is disclosure
withdrawal: a metric management gave every quarter and has stopped giving.
Nothing is said when that happens -- a number simply stops appearing -- so it is
invisible in any single call and only exists as a comparison across several.

U7 reads the GAAP-to-adjusted reconciliation across five periods, for the same
reason. An item excluded once is a one-off. The same item excluded four periods
running is a permanent cost that has been moved below the line, and the
reconciliation is the only place that can be seen.

U9 scores the analyst question-and-answer sections for stance and conviction.
Its output moves uncertainty and nothing else: it is never passed into the driver
path, so sentiment cannot reach a revenue or cost line. That is guaranteed here by
not wiring it, rather than by asking a model not to use it.
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
from .b_acquire import Dossier

STAGE = "B_SCAN"

CHANGE = schema.obj(
    {
        "observation": schema.text("what changed across the sequence"),
        "kind": schema.text("disclosure_withdrawn | language_shift | driver_reordered | promise_moved | repeated_question"),
        "first_seen": schema.text("date of the earlier call"),
        "last_seen": schema.text("date of the later call"),
        "quotes": schema.array_of(schema.text("verbatim quote with its call date"), "at least two, from two different calls"),
    }
)

SCAN_CALLS = schema.obj(
    {
        "changes": schema.array_of(CHANGE, "what changed across the sequence; an empty list is a real answer"),
        "summary": schema.text("one paragraph on what the sequence shows"),
    }
)

BRIDGE_ITEM = schema.obj(
    {
        "period": schema.text("the period this reconciliation covers"),
        "label": schema.text("the item, in the company's own words"),
        "amount": schema.nullable("number", "the amount as stated, or null"),
        "direction": schema.text("add_back | exclusion | unstated"),
        "quote": schema.text("verbatim"),
    }
)

BRIDGE = schema.obj(
    {
        "items": schema.array_of(BRIDGE_ITEM, "every reconciling item found"),
        "recurring_items": schema.array_of(
            schema.obj(
                {
                    "label": schema.text("the item that recurs"),
                    "periods": schema.number("how many of the periods examined contain it"),
                    "reading": schema.text("what its recurrence means"),
                }
            ),
            "items appearing in several periods — these are the finding",
        ),
    }
)

PERCEPTION = schema.obj(
    {
        "stance": schema.text("probing_downside | testing_upside | neutral"),
        "conviction": schema.number("0 to 1, how settled the view appears"),
        "recurring_concerns": schema.array_of(
            schema.text("something questioners keep returning to"),
            "where the market's uncertainty actually sits",
        ),
        "reasoning": schema.text("what the questions reveal about what the market thinks it knows"),
    }
)


@dataclass
class Scans:
    calls: dict[str, Any] | None = None
    bridge: dict[str, Any] | None = None
    perception: dict[str, Any] | None = None
    failures: list[dict[str, str]] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return {
            "calls": self.calls,
            "bridge": self.bridge,
            "perception": self.perception,
            "failures": self.failures,
        }


def run(ctx: RunContext, dossier: Dossier, client: Client, prompts: PromptStore) -> Scans:
    started = time.monotonic()
    ctx.events.emit(STAGE, "stage_started", nodes=["U7", "U8", "U9"], ticker=ctx.ticker)

    result = Scans()
    jobs = [
        ("U8", "scan_calls", SCAN_CALLS, _call_sequence(dossier), 10000),
        ("U7", "extract_bridge", BRIDGE, _releases(dossier), 8000),
        ("U9", "scan_perception", PERCEPTION, _qanda(dossier), 6000),
    ]

    def scan(job) -> tuple[str, dict[str, Any]]:
        node, agent, response_schema, body, max_tokens = job
        if not body.strip():
            raise ValueError(f"{agent}: no documents of the right kind were in the dossier")
        prompt = prompts.get(agent)
        response = client.call(
            stage=STAGE,
            node=node,
            agent=agent,
            tier_name="cheap",
            system_blocks=[plain(prompt.render(company=ctx.target.company, ticker=ctx.ticker))],
            user_text=body,
            response_schema=response_schema,
            max_tokens=max_tokens,
            prompt_version=prompt.version,
        )
        return agent, response.value

    for job, outcome in zip(jobs, fan_out(jobs, scan, events=ctx.events, stage=STAGE,
                                          label=lambda j: j[1], max_workers=3)):
        node, agent = job[0], job[1]
        if isinstance(outcome, Exception):
            result.failures.append({"agent": agent, "node": node, "reason": str(outcome)})
            ctx.note(
                STAGE,
                "degrade",
                f"{agent} produced nothing ({outcome}); what it would have contributed is absent "
                "rather than substituted",
                node=node,
            )
            continue
        setattr(result, {"scan_calls": "calls", "extract_bridge": "bridge",
                         "scan_perception": "perception"}[outcome[0]], outcome[1])

    if result.bridge and result.bridge.get("recurring_items"):
        for item in result.bridge["recurring_items"]:
            ctx.events.emit(
                STAGE, "recurring_exclusion", node="U7",
                label=item.get("label"), periods=item.get("periods"),
            )

    ctx.events.emit(
        STAGE,
        "stage_finished",
        duration_s=round(time.monotonic() - started, 3),
        produced=sum(1 for v in (result.calls, result.bridge, result.perception) if v),
        failed=len(result.failures),
    )
    return result


def _call_sequence(dossier: Dossier) -> str:
    parts = []
    for index, call in enumerate(dossier.call_sequence):
        parts.append(f"\n\n===== CALL {index + 1} of {len(dossier.call_sequence)} — {call.held_on.isoformat()} =====\n")
        for section in call.sections:
            parts.append(f"\n--- {section.segment or 'section'} ---\n{section.document.text()[:22_000]}")
    return "".join(parts)


def _releases(dossier: Dossier) -> str:
    return "\n\n===== =====\n\n".join(
        f"# {doc.title} ({doc.published_at.isoformat()})\n\n{doc.text()[:26_000]}"
        for doc in dossier.earnings_releases[:5]
    )


def _qanda(dossier: Dossier) -> str:
    parts = []
    for call in dossier.call_sequence[-5:]:
        for section in call.sections:
            if section.segment == "qna":
                parts.append(f"\n===== Q&A — {call.held_on.isoformat()} =====\n{section.document.text()[:20_000]}")
    return "".join(parts)
