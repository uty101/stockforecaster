"""Stage F, challenge. One mid-tier call per surviving lens.

For each surviving view: develop the case into its strongest honest form, then
argue against it hard and in good faith. Both in the same call, both recorded.

Comparing raw findings and taking the plurality rewards the finding that is
easiest to reach, not the one that matters. Every case is argued against before
anything is compared, and this is the stage that does it.

The stated confidence is kept alongside the surviving confidence rather than
overwritten, because the gap between them is itself informative and in practice it
is large. A view whose confidence does not move under attack has usually not been
attacked.

The surviving confidence reaches the judge as a discount on a view's weight, never
as a vote. Materiality stays primary. Without that wiring this stage would produce
a number that only reached a sheet, which would make up to nine mid-tier calls
decorative.
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
from .e_analyse import LensView
from .v1_reconcile import Reconciliation

STAGE = "F"

CHALLENGE = schema.obj(
    {
        "strongest_case": schema.text("the view at its strongest honest form"),
        "strongest_attack": schema.text("the best good-faith case that it is wrong"),
        "survived": schema.text("what still holds after the attack"),
        "broke": schema.text("what did not hold"),
        "surviving_confidence": schema.number("0 to 1, what is left standing"),
    }
)


@dataclass
class Challenge:
    lens: str
    node_id: str
    stated_confidence: float | None
    surviving_confidence: float
    payload: dict[str, Any]

    @property
    def erosion(self) -> float | None:
        if self.stated_confidence is None:
            return None
        return round(self.stated_confidence - self.surviving_confidence, 4)

    def to_json(self) -> dict[str, Any]:
        return {
            "lens": self.lens,
            "node_id": self.node_id,
            "stated_confidence": self.stated_confidence,
            "surviving_confidence": self.surviving_confidence,
            "erosion": self.erosion,
            **self.payload,
        }


@dataclass
class Challenges:
    items: list[Challenge] = field(default_factory=list)
    failures: list[dict[str, str]] = field(default_factory=list)

    def weight_for(self, lens: str) -> float:
        """A discount on the lens's weight, never a vote."""
        for item in self.items:
            if item.lens == lens:
                return item.surviving_confidence
        return 1.0

    def to_json(self) -> dict[str, Any]:
        return {
            "challenges": [item.to_json() for item in self.items],
            "failures": self.failures,
            "counts": {"challenged": len(self.items), "failed": len(self.failures)},
        }


def run(
    ctx: RunContext,
    reconciliation: Reconciliation,
    client: Client,
    prompts: PromptStore,
) -> Challenges:
    started = time.monotonic()
    ctx.events.emit(STAGE, "stage_started", nodes=["U22"], ticker=ctx.ticker)

    result = Challenges()
    prompt = prompts.get("champion")
    question = prompt.render(
        company=ctx.target.company, ticker=ctx.ticker, period=ctx.target.period
    )

    def challenge_one(view: LensView) -> Challenge:
        response = client.call(
            stage=STAGE,
            node="U22",
            agent="champion",
            tier_name="mid",
            # One lens view and nothing else. The champion cannot see the other
            # lenses or consensus, so it cannot smuggle a second opinion in.
            system_blocks=[plain(question)],
            user_text=_view_text(view),
            response_schema=CHALLENGE,
            max_tokens=8000,
            effort="medium",
            prompt_version=prompt.version,
        )
        return Challenge(
            lens=view.lens,
            node_id=view.node_id,
            stated_confidence=_stated(view),
            surviving_confidence=float(response.value["surviving_confidence"]),
            payload=response.value,
        )

    outcomes = fan_out(
        reconciliation.surviving,
        challenge_one,
        events=ctx.events,
        stage=STAGE,
        label=lambda view: view.lens,
        max_workers=6,
    )

    for view, outcome in zip(reconciliation.surviving, outcomes):
        if isinstance(outcome, Exception):
            result.failures.append({"lens": view.lens, "reason": str(outcome)})
            ctx.note(
                STAGE,
                "degrade",
                f"{view.lens} was not challenged ({outcome}); its view reaches the judge with its "
                "own stated confidence, which has not been tested",
                lens=view.lens,
            )
            continue
        result.items.append(outcome)
        if outcome.erosion is not None and outcome.erosion > 0.3:
            ctx.events.emit(
                STAGE,
                "confidence_eroded",
                node="U22",
                lens=outcome.lens,
                stated=outcome.stated_confidence,
                surviving=outcome.surviving_confidence,
            )

    ctx.events.emit(
        STAGE,
        "stage_finished",
        duration_s=round(time.monotonic() - started, 3),
        **result.to_json()["counts"],
    )
    return result


def _stated(view: LensView) -> float | None:
    values = [
        float(estimate["confidence"])
        for estimate in view.payload.get("estimates", [])
        if estimate.get("confidence") is not None
    ]
    return round(sum(values) / len(values), 4) if values else None


def _view_text(view: LensView) -> str:
    lines = [f"# The {view.lens} view", ""]
    for estimate in view.payload.get("estimates", []):
        value = estimate.get("estimate")
        lines.append(
            f"## {estimate.get('metric_label')}: "
            f"{'abstained' if value is None else value} "
            f"(stated confidence {estimate.get('confidence')})"
        )
        lines.append(estimate.get("reasoning", ""))
        for citation in estimate.get("citations", []) or []:
            lines.append(f"- cites [{citation.get('citation_id')}]: \"{citation.get('quote')}\"")
        lines.append("")
    lines.append(f"It says this would change its mind: {view.payload.get('what_would_change_my_mind', '')}")
    return "\n".join(lines)
