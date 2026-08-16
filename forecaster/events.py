"""The event tape: append-only JSONL, flushed on every write.

The site polls this file four times a second and replay reads it back through the
same rendering path a live run uses. Every degrade and every drop emits here at
the moment of loss, never as an end-of-run summary.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class EventSink:
    path: Path
    _handle: Any = field(default=None, repr=False)
    _mirror: list[dict[str, Any]] = field(default_factory=list, repr=False)

    def __post_init__(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = self.path.open("a", encoding="utf-8")

    def emit(self, stage: str, event: str, **payload: Any) -> dict[str, Any]:
        record: dict[str, Any] = {
            "ts": time.time(),
            "stage": stage,
            "event": event,
        }
        record.update(payload)
        line = json.dumps(record, default=str)
        self._handle.write(line + "\n")
        self._handle.flush()
        os.fsync(self._handle.fileno())
        self._mirror.append(record)
        return record

    @property
    def records(self) -> list[dict[str, Any]]:
        """In-memory mirror of everything emitted, for tests and for stage I."""
        return list(self._mirror)

    def close(self) -> None:
        if self._handle is not None:
            self._handle.close()
            self._handle = None


class NullSink(EventSink):
    """A sink that keeps the mirror but writes no file. Tests only."""

    def __init__(self) -> None:  # noqa: D107 - deliberately bypasses EventSink.__init__
        self.path = Path("<null>")
        self._handle = None
        self._mirror = []

    def emit(self, stage: str, event: str, **payload: Any) -> dict[str, Any]:
        record: dict[str, Any] = {"ts": time.time(), "stage": stage, "event": event}
        record.update(payload)
        self._mirror.append(record)
        return record

    def close(self) -> None:
        return
