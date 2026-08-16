"""The orchestrator. Every stage in order, top to bottom, one level of indentation.

No registry, no dispatch loop, no plugin discovery. A registry would save twenty
lines and destroy the property that matters: this function can be read out loud,
in order, while somebody watches over your shoulder.

The intermediate names are the data flow. Reading only the assignments tells you
what the system does:

    sources -> dossier -> extracted -> evidence -> model -> lens_views
            -> reconciled -> judged -> positioned -> results
"""

from __future__ import annotations

import time
import uuid
from datetime import date
from pathlib import Path
from typing import Any

from .agents.cache import ResponseCache
from .agents.client import Client
from .agents.prompts import PromptStore
from .agents.tiers import TierRouter
from .config import REPO_ROOT, Config, Target, load_config, load_targets
from .context import RunContext
from .events import EventSink
from .ledger import CostLedger
from .stages import (
    b3_research,
    b5_extract,
    b_acquire,
    c_structure,
    d_model,
    e_analyse,
    g_judge,
    h_position,
    i_output,
    v1_reconcile,
)
from .stages.a_sources import run as run_sources
from .stages.h_position import ConsensusBus


def forecast(target: Target, config: Config, runs_root: Path, run_id: str) -> Path:
    """One company, twelve fields of a results file, top to bottom."""
    run_dir = runs_root / f"{target.ticker.replace(':', '_')}-{config.as_of.isoformat()}-{run_id}"
    run_dir.mkdir(parents=True, exist_ok=True)

    ctx = RunContext(
        target=target,
        as_of=config.as_of,
        config=config,
        events=EventSink(run_dir / "events.jsonl"),
        ledger=CostLedger(ceiling_usd=float(config.section("cost")["ceiling_usd"])),
        run_dir=run_dir,
        run_id=run_id,
    )
    prompts = PromptStore()
    client = Client(
        router=TierRouter(config.section("models")),
        ledger=ctx.ledger,
        cache=ResponseCache(REPO_ROOT / config.section("cost")["cache_dir"]),
        events=ctx.events,
    )

    started = time.monotonic()
    ctx.events.emit("RUN", "run_started", ticker=ctx.ticker, as_of=ctx.as_of.isoformat())
    last_stage = "A"

    sources = run_sources(ctx)
    last_stage = "B"

    dossier = b_acquire.run(ctx, sources)
    b3_research.run(ctx, dossier, sources.chain, client, prompts)
    last_stage = "B5"

    extracted = b5_extract.run(ctx, dossier, client, prompts)
    last_stage = "C"

    consensus = sources.chain.fetch("consensus", ctx.ticker, ctx.target.period)
    evidence = c_structure.run(ctx, dossier, extracted, consensus)
    last_stage = "D"

    model = d_model.run(ctx, evidence)
    last_stage = "E"

    lens_views = e_analyse.run(ctx, evidence, model, client, prompts)
    last_stage = "V1"

    reconciled = v1_reconcile.run(ctx, lens_views, evidence)
    last_stage = "G"

    judged = g_judge.run(ctx, reconciled, lens_views, model, client, prompts)
    last_stage = "H"

    # The bus is constructed here and handed to exactly one stage. No reasoning
    # stage above takes it as an argument, which is what makes the isolation
    # structural rather than a promise.
    bus = ConsensusBus(tapped_at_node="U1", raw=consensus)
    positioned = h_position.run(ctx, judged, model, bus)
    last_stage = "I"

    results = i_output.run(
        ctx,
        dossier=dossier,
        evidence=evidence,
        model=model,
        views=lens_views,
        reconciliation=reconciled,
        judgement=judged,
        positions=positioned,
        extractions=extracted,
        integrity=sources.chain.integrity_record(),
        prompt_versions=prompts.versions(),
        last_stage=last_stage,
    )

    ctx.events.emit(
        "RUN",
        "run_finished",
        duration_s=round(time.monotonic() - started, 2),
        cost_usd=round(ctx.ledger.spent_usd, 4),
        results=str(results),
    )
    ctx.events.close()
    return results


def forecast_all(config: Config | None = None, runs_root: Path | None = None) -> dict[str, Any]:
    config = config or load_config()
    runs_root = runs_root or REPO_ROOT / "runs"
    run_id = uuid.uuid4().hex[:8]

    written: dict[str, Any] = {"run_id": run_id, "as_of": config.as_of.isoformat(), "companies": {}}
    for target in load_targets():
        try:
            path = forecast(target, config, runs_root, run_id)
            written["companies"][target.ticker] = {"results": str(path), "status": "ok"}
        except Exception as error:  # noqa: BLE001
            # One company failing must not cost the other three their numbers.
            written["companies"][target.ticker] = {"status": "failed", "error": str(error)}
    return written


if __name__ == "__main__":
    import json

    print(json.dumps(forecast_all(), indent=2))
