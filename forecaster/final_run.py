"""The final command. One invocation, four workbooks, one log file.

    py -m forecaster.final_run

Everything the run does lands in a new timestamped file under logs/. Nothing
there is ever overwritten, so a retry at 17:52 leaves the 17:31 attempt exactly
where it was -- which is the point, because the interesting log is usually the
one from the attempt that failed.

The order is fixed and each step gates the next: pipeline, workbooks,
verification. The verification step is the only thing that decides the exit code,
because "the workbooks on disk are the shape the organisers asked for" is the
only claim this command is actually in a position to make. It cannot tell you the
numbers are right.

Exit codes, which are in the log footer as well as here:

    0   four workbooks written and verified, every company forecast fresh
    1   a workbook failed verification -- do not upload
    2   four workbooks verified, but at least one company failed and its numbers
        were carried forward from the previous run rather than forecast today
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import REPO_ROOT, load_config, load_targets
from .env import EnvReport, load_env
from .run import forecast_all
from .runlog import RunLog

NODE = "node"
WORKBOOK_WRITER = Path("src") / "forecast-to-workbooks.mjs"
WORKBOOK_CHECKER = Path("scripts") / "check-forecasts.mjs"


# -- the steps -----------------------------------------------------------


def _header(log: RunLog, argv: list[str], env: EnvReport) -> None:
    log.rule("final run")
    log.field("log file", log.path)
    log.field("started (UTC)", log.started_at.isoformat())
    log.field("repo root", REPO_ROOT)
    # Names only. The values are secrets, and the log records which credentials
    # the run had rather than what they were.
    log.field("env file", env.describe())
    if env.loaded:
        log.field("  loaded from file", ", ".join(sorted(env.loaded)))
    if env.already_set:
        log.field("  already in shell", ", ".join(sorted(env.already_set)))
    log.field("ANTHROPIC_API_KEY", "present" if os.environ.get("ANTHROPIC_API_KEY") else "MISSING")

    commit, dirty = _git_state()
    log.field("git commit", commit)
    log.field("working tree", "clean" if not dirty else f"DIRTY -- {len(dirty)} file(s) modified")
    for entry in dirty:
        log.line(f"    {entry}")

    log.field("command", subprocess.list2cmdline([sys.executable, "-m", "forecaster.final_run", *argv]))
    log.field("argv", json.dumps([sys.argv[0], *argv]))
    log.field("python", f"{sys.version.split()[0]} at {sys.executable}")

    config = load_config()
    log.field("as_of", config.as_of.isoformat())
    log.field("corpus frozen", config.corpus_frozen_at.isoformat())
    log.field("cost ceiling", f"${config.section('cost')['ceiling_usd']}")

    targets = load_targets()
    log.line("")
    log.line(f"  {len(targets)} companies queued:")
    for index, target in enumerate(targets, start=1):
        metrics = ", ".join(f"{m.label} ({m.units})" for m in target.metrics)
        log.line(f"    {index}. {target.ticker:<8} {target.company:<20} {target.period:<10} -> {target.output_file}")
        log.line(f"       metrics: {metrics}")
    log.line("")
    log.line("  Per-company detail below is the event tape as it is emitted. The complete")
    log.line("  tape, including every dropped extraction, is in each run's events.jsonl.")


def _git_state() -> tuple[str, list[str]]:
    def git(args: list[str]) -> str:
        try:
            return subprocess.run(
                ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, timeout=30
            ).stdout.strip()
        except (OSError, subprocess.SubprocessError) as error:
            return f"<git unavailable: {error}>"

    commit = git(["rev-parse", "HEAD"])
    status = git(["status", "--porcelain"])
    return commit, [line for line in status.split("\n") if line.strip()]


def _pipeline(log: RunLog) -> dict[str, Any]:
    log.rule("pipeline")

    def on_failure(ticker: str, error: BaseException, retry_follows: bool) -> None:
        log.exception(error, context=f"company {ticker}", retry_follows=retry_follows)

    written = forecast_all(observer_for=log.observer, on_failure=on_failure)

    log.rule("pipeline summary")
    log.field("run id", written["run_id"])
    for ticker, outcome in written["companies"].items():
        status = outcome.get("status")
        attempts = outcome.get("attempts")
        suffix = "" if attempts == 1 else f" after {attempts} attempts"
        if status == "ok":
            results = Path(outcome["results"])
            log.field(ticker, f"OK{suffix}")
            log.line(f"    results   {results}")
            log.line(f"    cost      ${_cost_of(results):.4f}")
            log.line(f"    stage     {_last_stage_of(results)}")
        else:
            log.field(ticker, f"FAILED{suffix} -- {outcome.get('error_type')}: {outcome.get('error')}")
            log.line("    results   none written; the workbook step will carry forward if it can")
    return written


def _cost_of(results: Path) -> float:
    try:
        return float(json.loads(results.read_text(encoding="utf-8"))["cost"]["spent_usd"])
    except (OSError, ValueError, KeyError):
        return 0.0


def _last_stage_of(results: Path) -> str:
    try:
        return str(json.loads(results.read_text(encoding="utf-8"))["header"]["last_completed_stage"])
    except (OSError, ValueError, KeyError):
        return "unknown"


def _node(log: RunLog, script: Path, *args: str) -> tuple[int, list[str]]:
    """Run a node script, teeing both of its streams into the log as they arrive.

    Two reader threads rather than merging into one pipe, because the checker
    prints PASS on stdout and FAIL on stderr and collapsing those loses the one
    distinction the step exists to make.
    """
    log.line(f"$ {NODE} {script} {' '.join(args)}".rstrip())
    collected: list[str] = []
    lock = threading.Lock()

    try:
        process = subprocess.Popen(
            [NODE, str(script), *args],
            cwd=REPO_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
    except OSError as error:
        log.exception(error, context=f"launching {script}", retry_follows=False)
        return 127, collected

    def pump(stream: Any, channel: str) -> None:
        for raw in stream:
            line = raw.rstrip("\n")
            with lock:
                collected.append(line)
                log.line(line, channel=channel)
        stream.close()

    threads = [
        threading.Thread(target=pump, args=(process.stdout, "out"), daemon=True),
        threading.Thread(target=pump, args=(process.stderr, "err"), daemon=True),
    ]
    for thread in threads:
        thread.start()
    code = process.wait()
    for thread in threads:
        thread.join(timeout=30)

    log.line(f"  exit code {code}")
    return code, collected


def _workbooks(log: RunLog) -> int:
    log.rule("workbooks")
    code, _ = _node(log, WORKBOOK_WRITER)
    return code


def _verify(log: RunLog) -> tuple[bool, dict[str, str]]:
    log.rule("verification")
    _, output = _node(log, WORKBOOK_CHECKER, "submission")

    verdicts: dict[str, str] = {}
    for line in output:
        if line.startswith("PASS "):
            verdicts[line[5:].strip()] = "PASS"
        elif line.startswith("FAIL "):
            name, _, detail = line[5:].partition(":")
            name = name.strip()
            # Several failures can be reported for one file; keep them all.
            existing = verdicts.get(name, "")
            reason = detail.strip()
            verdicts[name] = (
                f"FAIL: {reason}" if not existing.startswith("FAIL") else f"{existing}; {reason}"
            )

    log.rule("workbooks written")
    every_file_passed = True
    for target in load_targets():
        path = REPO_ROOT / "submission" / target.output_file
        exists = path.exists()
        size = path.stat().st_size if exists else 0
        verdict = verdicts.get(target.output_file, "FAIL: the checker reported no verdict for this file")
        if not verdict.startswith("PASS"):
            every_file_passed = False
        log.field(target.ticker, verdict)
        log.line(f"    path      {path}")
        log.line(f"    on disk   {'yes' if exists else 'NO'}, {size} bytes")
    return every_file_passed, verdicts


# -- entry point ---------------------------------------------------------


def _run(log: RunLog, argv: list[str], env: EnvReport) -> int:
    _header(log, argv, env)
    written = _pipeline(log)
    _workbooks(log)
    passed, _ = _verify(log)

    failures = [t for t, o in written["companies"].items() if o.get("status") != "ok"]
    retried = [t for t, o in written["companies"].items() if (o.get("attempts") or 1) > 1]

    log.rule("outcome")
    log.field("companies forecast", f"{len(written['companies']) - len(failures)}/{len(written['companies'])}")
    log.field("companies retried", ", ".join(retried) or "none")
    log.field("companies failed", ", ".join(failures) or "none")
    log.field("workbooks verified", "all four PASS" if passed else "NOT all four -- do not upload")

    if not passed:
        code = 1
        meaning = "a workbook failed verification; do not upload"
    elif failures:
        code = 2
        meaning = (
            f"workbooks verified, but {', '.join(failures)} failed and carried forward "
            "previous numbers rather than forecasting today"
        )
    else:
        code = 0
        meaning = "four workbooks written, verified, and every number forecast in this run"

    log.field("exit code", f"{code} -- {meaning}")
    log.field("uploads", "manual by rule; the agent does not post to OpenStocks")
    return code


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    # Before the log exists, and that order is load-bearing: RunLog builds its
    # Redactor from os.environ at construction, so a key loaded after this point
    # would be a key the redactor cannot scrub.
    env = load_env()
    log = RunLog(REPO_ROOT / "logs", started_at=datetime.now(timezone.utc))
    # Printed before the swap so it reaches the terminal even if the tee itself
    # is what goes wrong.
    print(f"logging to {log.path}")
    try:
        with log.tee():
            return _run(log, argv, env)
    except BaseException as error:  # noqa: BLE001 - a crash must still leave a footer
        # The tee has already written the traceback into the file on its way out.
        # This exists so the terminal says where to look rather than scrolling a
        # stack trace past somebody who has forty minutes left.
        print(f"final run FAILED: {type(error).__name__}: {error}", file=sys.stderr)
        print(f"full traceback and everything before it: {log.path}", file=sys.stderr)
        return 1
    finally:
        log.line(f"log closed: {log.path}")
        log.close()


if __name__ == "__main__":
    raise SystemExit(main())
