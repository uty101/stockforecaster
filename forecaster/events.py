"""The event tape: append-only JSONL, flushed on every write.

The site polls this file four times a second and replay reads it back through the
same rendering path a live run uses. Every degrade and every drop emits here at
the moment of loss, never as an end-of-run summary.

An optional observer is called with each record as it is emitted. The run log
uses it, which is why no stage takes a logger argument: the tape is already the
complete account of what happened, so a second reporting path through the stages
would be a second thing to keep in step with the first.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable


@dataclass
class EventSink:
    path: Path
    observer: Callable[[dict[str, Any]], None] | None = None
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
        self._notify(record)
        return record

    def _notify(self, record: dict[str, Any]) -> None:
        """The tape is the primary record; the observer is a reader of it.

        A failing observer must never take the run down with it, because the log
        is there to describe the run and not to be a new way for it to die. The
        failure is written onto the tape itself, which is the one place still
        guaranteed to be working.
        """
        if self.observer is None:
            return
        try:
            self.observer(record)
        except Exception as error:  # noqa: BLE001
            self.observer = None
            failure = {
                "ts": time.time(),
                "stage": "RUN",
                "event": "observer_failed",
                "error": f"{type(error).__name__}: {error}",
                "message": "run log observer detached; the tape is unaffected",
            }
            self._handle.write(json.dumps(failure, default=str) + "\n")
            self._handle.flush()
            self._mirror.append(failure)

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

    def __init__(
        self, observer: Callable[[dict[str, Any]], None] | None = None
    ) -> None:  # noqa: D107 - deliberately bypasses EventSink.__init__
        self.path = Path("<null>")
        self.observer = observer
        self._handle = None
        self._mirror = []

    def emit(self, stage: str, event: str, **payload: Any) -> dict[str, Any]:
        record: dict[str, Any] = {"ts": time.time(), "stage": stage, "event": event}
        record.update(payload)
        self._mirror.append(record)
        if self.observer is not None:
            self.observer(record)
        return record

    def close(self) -> None:
        return
