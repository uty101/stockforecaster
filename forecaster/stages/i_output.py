"""Stage I, output. Raise.

Writes the results file and closes the event tape.

Written atomically: temporary file in the same directory, flush, fsync, rename.
Without that the site eventually polls mid-write and parses a truncated document,
which surfaces as a confusing complaint about an unexpected token rather than
anything pointing at the cause.

The results file carries every field any sheet renders, including the rationale
strings, the condition names, the drop reasons and the status labels. The site
computes nothing, so wanting to compute something there means a field is missing
here.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from .. import nodes
from ..context import RunContext

STAGE = "I"


def write_atomic(path: Path, payload: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, default=str)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)
    return path


def run(
    ctx: RunContext,
    *,
    dossier: Any,
    evidence: Any,
    model: Any,
    views: Any,
    reconciliation: Any,
    judgement: Any,
    positions: Any,
    challenges: Any,
    comparability: Any,
    extractions: Any,
    integrity: dict[str, Any],
    prompt_versions: dict[str, str],
    last_stage: str,
) -> Path:
    started = time.monotonic()
    ctx.events.emit(STAGE, "stage_started", nodes=["OUT"], ticker=ctx.ticker)

    payload = {
        "header": {
            "ticker": ctx.ticker,
            "company": ctx.target.company,
            "period": ctx.target.period,
            "period_kind": ctx.target.period_kind,
            "as_of": ctx.as_of.isoformat(),
            "run_id": ctx.run_id,
            "fixture": False,
            "last_completed_stage": last_stage,
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        },
        "forecast": {
            "metrics": [item.to_json() for item in positions.items],
            "consensus_bus": positions.to_json()["consensus_bus"],
        },
        "challenge": challenges.to_json(),
        "comparability": comparability.to_json(),
        "judge": judgement.to_json(),
        "lenses": views.to_json(),
        "reconciliation": reconciliation.to_json(),
        "model": model.to_json(),
        "evidence": evidence.to_json(),
        "extractions": extractions.to_json(),
        "dossier": dossier.to_json(),
        "integrity": integrity,
        "cost": {
            "ceiling_usd": ctx.ledger.ceiling_usd,
            "spent_usd": round(ctx.ledger.spent_usd, 4),
            "deep_calls": ctx.ledger.deep_calls,
            "calls": [call.__dict__ for call in ctx.ledger.calls],
            "per_stage": ctx.ledger.per_stage(),
        },
        "method": {
            "prompt_versions": prompt_versions,
            "beta_measured": bool(ctx.config.section("lambda").get("beta_measured")),
            "backtest": {
                "runs": 0,
                "status": "open",
                "why": (
                    "Scoring our own estimate against past actuals is possible from the filings, "
                    "but scoring it against the Street bar as it stood at those quarters is not: "
                    "no source available to this build publishes consensus history. Skill against "
                    "consensus is therefore unobtainable rather than merely unmeasured, and the "
                    "lambda beta stays asserted."
                ),
            },
        },
        "notes": ctx.notes,
        "nodes": nodes.summary(),
    }

    path = write_atomic(ctx.run_dir / "results.json", payload)
    ctx.events.emit(
        STAGE,
        "stage_finished",
        duration_s=round(time.monotonic() - started, 3),
        cost_usd=0.0,
        results=str(path),
        bytes=path.stat().st_size,
    )
    return path
