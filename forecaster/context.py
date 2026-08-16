"""The run context: the only argument every stage shares, and the only thing a
stage may mutate.

The specification says the context carries the ticker. It carries a Target
instead, because the unit of work here is a company-period owing three metrics
with their units and a period label, and splitting that across the context and
a second argument is how a forecast ends up written against the wrong metric.
Everything else is as specified: as-of date, config, event sink, cost ledger,
run directory. Nothing else lives here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from .config import Config, Target
from .events import EventSink
from .ledger import CostLedger


@dataclass
class RunContext:
    target: Target
    as_of: date
    config: Config
    events: EventSink
    ledger: CostLedger
    run_dir: Path
    run_id: str
    notes: list[dict[str, Any]] = field(default_factory=list)

    @property
    def ticker(self) -> str:
        return self.target.ticker

    def note(self, stage: str, kind: str, message: str, **payload: Any) -> None:
        """Append-only record of anything a sheet has to say out loud.

        Degrade and drop both land here as well as on the tape, because the tape
        is chronological and the sheets need the losses grouped by stage.
        """
        record = {"stage": stage, "kind": kind, "message": message, **payload}
        self.notes.append(record)
        self.events.emit(stage, kind, message=message, **payload)
