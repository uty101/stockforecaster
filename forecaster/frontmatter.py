"""Read the YAML-style frontmatter block that every corpus document carries.

This is deliberately not a YAML parser. The corpus writes a fixed, flat block:

    ---
    company: "Home Depot"
    published_at: "2026-05-19"
    source_url: null
    ---

so a strict reader for exactly that shape is safer here than a hand-rolled YAML
subset, which would accept malformed input and invent a value for it. Anything
that does not match `key: value` on one line is reported, not guessed at.
"""

from __future__ import annotations

from pathlib import Path


class FrontmatterError(Exception):
    """Raised when a document does not carry a readable frontmatter block."""


def parse_frontmatter(text: str, *, origin: str = "<string>") -> tuple[dict[str, str | None], str]:
    """Return (fields, body). Raises FrontmatterError rather than returning defaults."""
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        raise FrontmatterError(f"{origin}: no opening --- on the first line")

    fields: dict[str, str | None] = {}
    for index in range(1, len(lines)):
        line = lines[index]
        if line.strip() == "---":
            return fields, "\n".join(lines[index + 1 :])
        if not line.strip():
            continue
        if ":" not in line:
            raise FrontmatterError(f"{origin}: line {index + 1} is not `key: value`: {line!r}")
        key, _, raw = line.partition(":")
        fields[key.strip()] = _scalar(raw.strip())

    raise FrontmatterError(f"{origin}: frontmatter block was never closed with ---")


def read_frontmatter(path: Path) -> tuple[dict[str, str | None], str]:
    text = path.read_text(encoding="utf-8", errors="replace")
    return parse_frontmatter(text, origin=str(path))


def _scalar(raw: str) -> str | None:
    if raw in ("null", "~", ""):
        return None
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in ("'", '"'):
        return raw[1:-1]
    return raw
