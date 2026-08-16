"""The run log: one timestamped file per invocation of the final command.

Three things it has to survive, which between them decide the design.

A crash. So every line is written and fsynced at the moment it happens, never
buffered into an end-of-run summary. A log assembled at the end is exactly the
log you do not have when the run dies at stage E.

Both streams. stdout and stderr are teed rather than redirected, so the terminal
still shows the run live while the file records it, and a traceback printed by
the interpreter on the way down lands in the file like anything else.

A retry. The filename carries the start time and the file is opened with an
exclusive create, so a second attempt cannot open the first attempt's file even
if it starts inside the same second. Nothing is ever removed or truncated.

Everything on its way to disk goes through the redactor first. That is enforced
in the one method that writes -- `line` -- rather than at the call sites, because
a rule applied at call sites is a rule the next call site forgets.
"""

from __future__ import annotations

import json
import os
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from types import TracebackType
from typing import Any, Callable, TextIO

from .redact import Redactor

CHANNELS = ("log", "out", "err")

# Stages whose drops are counted rather than printed line by line. B5 drops one
# extracted value per failed quote match and there are routinely over a hundred
# per company: printed in full they bury the stage-B skips and the V1 lens drops,
# which are the two the reader is actually looking for. The count is printed at
# the stage boundary and the full list is in the run's events.jsonl, which the
# header line points at. Nothing is discarded, only moved.
SUMMARISE_DROPS_FROM = frozenset({"B5"})


def _utc_stamp(moment: datetime | None = None) -> str:
    moment = moment or datetime.now(timezone.utc)
    return f"{moment.strftime('%Y-%m-%dT%H:%M:%S')}.{moment.microsecond // 1000:03d}Z"


def _filename_stamp(moment: datetime | None = None) -> str:
    return (moment or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")


class RunLog:
    """An append-only, timestamped, redacted log file."""

    def __init__(
        self,
        directory: Path,
        prefix: str = "final-run",
        started_at: datetime | None = None,
        redactor: Redactor | None = None,
    ) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        self._redact = redactor or Redactor()
        self.started_at = started_at or datetime.now(timezone.utc)
        self.path, self._handle = self._open_new(directory, prefix, self.started_at)
        self._suppressed_drops: dict[tuple[str, str], int] = {}
        self._closed = False

    @staticmethod
    def _open_new(directory: Path, prefix: str, started_at: datetime) -> tuple[Path, TextIO]:
        """Create a file nothing else holds, and keep the handle that created it.

        Exclusive create rather than an existence check, and the handle from that
        same create rather than a second open: two runs started inside the same
        second would both pass a check, and closing and reopening leaves a window
        in which the other one takes the name. The suffix loop is bounded because
        an unbounded one on a full disk spins forever.
        """
        base = f"{prefix}-{_filename_stamp(started_at)}"
        for attempt in range(1, 1000):
            candidate = directory / (f"{base}.log" if attempt == 1 else f"{base}-{attempt}.log")
            try:
                return candidate, candidate.open("x", encoding="utf-8", newline="\n")
            except FileExistsError:
                continue
        raise RuntimeError(f"could not find an unused log name under {directory}")

    # -- writing ---------------------------------------------------------

    def line(self, text: str, channel: str = "log") -> None:
        """The only method that writes. Redaction and fsync both live here."""
        if self._closed:
            return
        stamp = _utc_stamp()
        for raw in str(text).replace("\r\n", "\n").split("\n"):
            scrubbed = self._redact(raw.rstrip("\r"))
            self._handle.write(f"{stamp} {channel:<3} | {scrubbed}\n")
        self._handle.flush()
        os.fsync(self._handle.fileno())

    def rule(self, title: str) -> None:
        self.line("")
        self.line(f"== {title} " + "=" * max(0, 72 - len(title)))

    def field(self, name: str, value: Any) -> None:
        self.line(f"  {name:<22} {value}")

    def exception(self, error: BaseException, *, context: str, retry_follows: bool) -> None:
        """The failure record. Both halves are required, so both are one call.

        `retry_follows` is not optional and has no default. A log that says a
        step failed but not whether anything tried again is the log that starts
        the argument about whether the number on the sheet came from a retry.
        """
        self.line(f"FAILURE {context}: {type(error).__name__}: {error}", channel="err")
        formatted = "".join(
            traceback.format_exception(type(error), error, error.__traceback__)
        ).rstrip()
        for raw in formatted.split("\n"):
            self.line(f"    {raw}", channel="err")
        self.line(
            f"  retry: {'yes, another attempt follows' if retry_follows else 'no, not retried'}",
            channel="err",
        )

    # -- the event tape --------------------------------------------------

    def observer(self, ticker: str) -> Callable[[dict[str, Any]], None]:
        """A sink observer bound to one company.

        Handed to EventSink so lines appear as the pipeline emits them. The
        pipeline is not aware of the log and does not gain a logging argument;
        it emits events either way and this decides which of them a human
        reading the file needs to see.
        """

        def observe(record: dict[str, Any]) -> None:
            described = self._describe(ticker, record)
            if described:
                self.line(f"[{ticker}] {record.get('stage', '?'):<3} {described}")

        return observe

    def _describe(self, ticker: str, record: dict[str, Any]) -> str | None:
        stage = str(record.get("stage", ""))
        event = str(record.get("event", ""))

        if event == "source_answered":
            fell = record.get("fell_through") or []
            after = f" (after {', '.join(fell)} declined)" if fell else ""
            return f"source: {record.get('request')} answered by {record.get('source')}{after}"

        if event == "source_fell_through":
            return (
                f"source: {record.get('source')} did not answer {record.get('request')} "
                f"-- {record.get('reason')}"
            )

        if event == "source_exhausted":
            tried = ", ".join(record.get("tried") or []) or "nothing"
            return f"source: NOTHING answered {record.get('request')}; tried {tried}"

        if event == "drop":
            if stage in SUMMARISE_DROPS_FROM:
                key = (ticker, stage)
                self._suppressed_drops[key] = self._suppressed_drops.get(key, 0) + 1
                return None
            if record.get("lens"):
                detail = f" (citation {record['citation_id']})" if record.get("citation_id") else ""
                return f"LENS DROPPED: {record.get('message')}{detail}"
            if record.get("acquirer"):
                ids = record.get("doc_ids") or []
                shown = ", ".join(ids[:6])
                more = f", +{len(ids) - 6} more" if len(ids) > 6 else ""
                return (
                    f"acquisition skipped: {record.get('message')} "
                    f"[reason: {record.get('reason')}] {shown}{more}"
                )
            return f"drop: {record.get('message')}"

        if event == "degrade":
            return f"degrade: {record.get('message')}"

        if event == "position":
            return self._describe_position(record)

        if event == "agent_retry":
            return (
                f"agent {record.get('agent')} attempt {record.get('attempt')} failed "
                f"-- {record.get('error')}; retry: yes, in {record.get('backoff_s')}s"
            )

        if event == "agent_failed":
            return (
                f"agent {record.get('agent')} failed after {record.get('attempts')} attempts "
                f"-- {record.get('error')}; retry: no, attempts exhausted"
            )

        if event == "agent_finished":
            return (
                f"agent {record.get('agent')} [{record.get('tier')}/{record.get('model')}] "
                f"{record.get('duration_s')}s ${record.get('cost_usd')} "
                f"in={record.get('input_tokens')} out={record.get('output_tokens')} "
                f"cache_w={record.get('cache_write_tokens')} cache_r={record.get('cache_read_tokens')}"
            )

        if event == "agent_cached":
            return f"agent {record.get('agent')} served from cache, $0.00"

        if event == "research_acquired":
            return f"research: {record.get('documents')} documents written to the run directory"

        if event == "stage_started":
            return f"-- stage {stage} started"

        if event == "stage_finished":
            extras = {
                key: value
                for key, value in record.items()
                if key not in ("ts", "stage", "event", "duration_s", "message")
            }
            suppressed = self._suppressed_drops.pop((ticker, stage), 0)
            note = (
                f" [{suppressed} value(s) dropped on quote mismatch; each one is in events.jsonl]"
                if suppressed
                else ""
            )
            return (
                f"-- stage {stage} finished in {record.get('duration_s')}s "
                f"{json.dumps(extras, default=str)}{note}"
            )

        if event == "run_started":
            return f"pipeline started, as_of {record.get('as_of')}"

        if event == "run_finished":
            return (
                f"pipeline finished in {record.get('duration_s')}s, "
                f"cost ${record.get('cost_usd')}, results {record.get('results')}"
            )

        return None

    def _describe_position(self, record: dict[str, Any]) -> str:
        fired = record.get("conditions") or []
        if fired:
            rendered = "; ".join(
                f"{item.get('name')} on input {item.get('input')} "
                f"x{item.get('multiplier')} -> {item.get('effect')}"
                for item in fired
            )
        else:
            rendered = "no regime condition fired"
        return (
            f"position: {record.get('metric')} lambda={record.get('lambda')} "
            f"(preset {record.get('preset')}) own={record.get('own_estimate')} "
            f"consensus={record.get('consensus')} forecast={record.get('forecast')} "
            f"| conditions: {rendered}"
        )

    # -- teeing ----------------------------------------------------------

    def tee(self) -> "_Tee":
        return _Tee(self)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._handle.flush()
        os.fsync(self._handle.fileno())
        self._handle.close()


class _Stream:
    """One teed stream. Writes through to the terminal and buffers to a line."""

    def __init__(self, wrapped: TextIO, log: RunLog, channel: str) -> None:
        self._wrapped = wrapped
        self._log = log
        self._channel = channel
        self._buffer = ""

    def write(self, text: str) -> int:
        written = self._wrapped.write(text)
        self._buffer += text
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            self._log.line(line, channel=self._channel)
        return written

    def flush(self) -> None:
        self._wrapped.flush()

    def drain(self) -> None:
        """Anything written without a trailing newline still has to be recorded."""
        if self._buffer:
            self._log.line(self._buffer, channel=self._channel)
            self._buffer = ""

    def isatty(self) -> bool:
        return getattr(self._wrapped, "isatty", lambda: False)()

    def fileno(self) -> int:
        return self._wrapped.fileno()

    @property
    def encoding(self) -> str:
        return getattr(self._wrapped, "encoding", "utf-8")


class _Tee:
    """Context manager that redirects both streams for the length of the run.

    The interpreter's own last words on an uncaught exception go to sys.stderr,
    so leaving the swap in place until after the top-level handler has run is
    what puts a fatal traceback in the file.
    """

    def __init__(self, log: RunLog) -> None:
        self._log = log
        self._saved: tuple[TextIO, TextIO] | None = None
        self.out: _Stream | None = None
        self.err: _Stream | None = None

    def __enter__(self) -> "_Tee":
        self._saved = (sys.stdout, sys.stderr)
        self.out = _Stream(sys.stdout, self._log, "out")
        self.err = _Stream(sys.stderr, self._log, "err")
        sys.stdout, sys.stderr = self.out, self.err  # type: ignore[assignment]
        return self

    def __exit__(
        self,
        kind: type[BaseException] | None,
        error: BaseException | None,
        trace: TracebackType | None,
    ) -> bool:
        if error is not None:
            self._log.exception(error, context="the run itself", retry_follows=False)
        for stream in (self.out, self.err):
            if stream is not None:
                stream.drain()
        if self._saved is not None:
            sys.stdout, sys.stderr = self._saved
        return False
