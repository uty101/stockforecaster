"""U5, industry research, driven by an agent rather than a fixed query list.

Two rounds. The scout proposes searches, we run them, and then the scout sees the
titles that came back and asks for what is still missing. That second round is
the point: a fixed query list cannot notice that every result came from the same
trade magazine, and a scout that sees its own results can.

Everything retrieved is written to the run directory as a document, so a lens
citing it is quoting text that exists on disk and V1 can string-match the quote
exactly as it matches a quote from an 8-K.

Re-running with a later as_of picks up everything published since, into a new run
directory, with the same code. That is the honest version of "search every day":
each run states the date it was locked to, and replaying an old run replays the
evidence that existed then rather than quietly acquiring newer material.
"""

from __future__ import annotations

import time
from datetime import date
from typing import Any

from ..agents import schema
from ..agents.client import Client, plain
from ..agents.prompts import PromptStore
from ..context import RunContext
from ..documents import RESEARCH, Document
from .b_acquire import Dossier

STAGE = "B3"

QUERY = schema.obj(
    {
        "query": schema.text("the search string, naming the driver rather than the company"),
        "angle": schema.text("demand or cost"),
        "expected_signal": schema.text("what this should tell us that we do not already know"),
    }
)

SCOUT = schema.obj({"queries": schema.array_of(QUERY, "the searches worth running")})

FIRST_ROUND = (
    "Propose {n} searches. Spread them across demand and cost, and across different "
    "kinds of publisher."
)

SECOND_ROUND = (
    "These searches have already run and returned the documents listed below.\n\n"
    "{found}\n\n"
    "Propose up to {n} further searches that close a gap this list leaves. If the list "
    "already covers demand and cost from independent publishers, return an empty list — "
    "that is a real answer and better than padding."
)


def run(
    ctx: RunContext,
    dossier: Dossier,
    chain: Any,
    client: Client,
    prompts: PromptStore,
    *,
    first_round: int = 6,
    second_round: int = 4,
) -> None:
    started = time.monotonic()
    ctx.events.emit(STAGE, "stage_started", nodes=["U5"], ticker=ctx.ticker)

    source = next((item for item in chain.sources if item.name == "research"), None)
    if source is None:
        ctx.note(STAGE, "degrade", "no research source is configured; the driver lenses run on company disclosure alone")
        return

    prompt = prompts.get("research_scout")
    seen_urls: set[str] = set()
    queries_run: list[dict[str, Any]] = []

    for round_index, (count, instruction) in enumerate(
        (
            (first_round, FIRST_ROUND.format(n=first_round)),
            (second_round, None),
        )
    ):
        if round_index == 1:
            if not dossier.research:
                break
            found = "\n".join(
                f"- [{doc.period}] {doc.published_at} · {doc.title}" for doc in dossier.research
            )
            instruction = SECOND_ROUND.format(found=found, n=second_round)

        try:
            response = client.call(
                stage=STAGE,
                node="U5",
                agent="research_scout",
                tier_name="cheap",
                system_blocks=[
                    plain(
                        prompt.render(
                            company=ctx.target.company,
                            ticker=ctx.ticker,
                            period_noun=ctx.target.period_noun,
                            metrics_block="\n".join(
                                f"- {m.label} ({m.units})" for m in ctx.target.metrics
                            ),
                            round_instruction=instruction,
                        )
                    )
                ],
                user_text=(
                    f"Company: {ctx.target.company} ({ctx.ticker}). "
                    f"Period: {ctx.target.period}. Lock date: {ctx.as_of.isoformat()}."
                ),
                response_schema=SCOUT,
                max_tokens=3000,
                prompt_version=prompt.version,
            )
        except Exception as error:  # noqa: BLE001
            ctx.note(
                STAGE,
                "degrade",
                f"the research scout failed on round {round_index + 1} ({error}); research falls "
                "back to the configured queries for this round",
            )
            break

        proposed = response.value.get("queries", [])[:count]
        if not proposed:
            break
        queries_run.extend(proposed)
        _retrieve(ctx, dossier, source, proposed, seen_urls)

    ctx.events.emit(
        STAGE,
        "stage_finished",
        duration_s=round(time.monotonic() - started, 3),
        documents=len(dossier.research),
        queries=len(queries_run),
        publishers=len({(doc.source_url or "").split("/")[2] for doc in dossier.research if doc.source_url}),
    )


def _retrieve(
    ctx: RunContext,
    dossier: Dossier,
    source: Any,
    queries: list[dict[str, Any]],
    seen_urls: set[str],
) -> None:
    folder = ctx.run_dir / "research"
    folder.mkdir(parents=True, exist_ok=True)

    for entry in queries:
        try:
            items = source.search_many([entry], ctx.as_of)
        except Exception as error:  # noqa: BLE001
            ctx.note(
                STAGE,
                "drop",
                f"search failed for {entry.get('query')!r}: {error}",
                query=entry.get("query"),
            )
            continue

        for item in items or []:
            if item["url"] in seen_urls:
                continue
            seen_urls.add(item["url"])
            path = folder / f"{item['slug']}.md"
            path.write_text(
                "---\n"
                f'company: "{dossier.company}"\n'
                f'ticker: "{ctx.ticker}"\n'
                f'published_at: "{item["published_at"]}"\n'
                f'document_type: "{RESEARCH}"\n'
                f'period: "{item["angle"]}"\n'
                f'source_url: "{item["url"]}"\n'
                "---\n\n"
                f"# {item['title']}\n\n{item['text']}\n",
                encoding="utf-8",
            )
            dossier.research.append(
                Document(
                    doc_id=item["slug"],
                    company=dossier.company,
                    ticker=ctx.ticker,
                    published_at=date.fromisoformat(item["published_at"]),
                    doc_type=RESEARCH,
                    period=item["angle"],
                    title=item["title"],
                    source_url=item["url"],
                    path=path,
                    source_name=item["source"],
                )
            )
