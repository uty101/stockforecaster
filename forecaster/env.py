"""Loading .env into the process environment.

entry.json declares the final command as a bare `py -m forecaster.final_run`.
That is the command the organisers may run, so it has to work on its own rather
than only inside the one terminal that happens to have the keys exported. Nothing
else in the package reads .env -- the client reads os.environ directly -- so this
is the single place that bridges the two.

Two rules, both of which matter more than they look.

The real environment wins. A variable already set is never replaced, so exporting
a key for one run still overrides the file, and the file cannot silently
substitute a stale key for the one somebody deliberately set.

This must run before the run log opens. Redactor snapshots os.environ when it is
constructed, so a key loaded after that point is a key the redactor has never
seen and will not scrub. Loading first is what keeps every value in this file out
of the log. The names are logged; the values are not, and are not passed to
anything that logs.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from .config import REPO_ROOT


@dataclass
class EnvReport:
    """What the load did, in terms safe to print. Names only, never values."""

    path: Path
    found: bool = False
    loaded: list[str] = field(default_factory=list)
    already_set: list[str] = field(default_factory=list)
    malformed: list[int] = field(default_factory=list)

    def describe(self) -> str:
        if not self.found:
            return f"{self.path} not present; using the ambient environment only"
        parts = [f"{len(self.loaded)} loaded"]
        if self.already_set:
            parts.append(f"{len(self.already_set)} already set and left alone")
        if self.malformed:
            parts.append(f"{len(self.malformed)} unparseable line(s) at {self.malformed}")
        return f"{self.path}: " + ", ".join(parts)


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def load_env(path: Path | None = None, environ: dict[str, str] | None = None) -> EnvReport:
    """Read KEY=VALUE lines into the environment without overriding what is there."""
    path = path or REPO_ROOT / ".env"
    environ = os.environ if environ is None else environ
    report = EnvReport(path=path)

    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return report
    except OSError:
        # An unreadable .env is worth reporting but is not worth refusing to run
        # over: the ambient environment may well already carry the keys.
        report.found = True
        report.malformed.append(0)
        return report

    report.found = True
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        name, separator, value = line.partition("=")
        name = name.strip()
        if not separator or not name:
            report.malformed.append(number)
            continue
        if name in environ and environ[name].strip():
            report.already_set.append(name)
            continue
        environ[name] = _unquote(value)
        report.loaded.append(name)
    return report
