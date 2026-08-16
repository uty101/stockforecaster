"""Secret redaction for anything on its way into a log file.

Two passes, in this order, because they fail in different directions.

The first pass replaces the literal value of every environment variable whose
name looks like a credential. That is the pass that actually protects us: it does
not care what shape the secret is, so an Exa key that is a bare UUID and an
Anthropic key that is prefixed are both caught, and a provider that changes its
key format tomorrow is caught too.

The second pass is pattern matching, and it exists only for secrets that never
passed through our environment -- a key pasted into a prompt, a token echoed back
inside an HTTP error body. It is a net under the first pass, not the main floor.

One rule deliberately absent: there is no blanket "long hex string" pattern. A
40-character hex string is a git commit hash, and the run log is required to
carry the commit hash. A redactor that eats the thing the log exists to record
has not made the log safer, it has made it useless. The environment pass covers
the two 32-hex API keys we actually hold, which is the reason that rule is not
needed in the first place.
"""

from __future__ import annotations

import os
import re

PLACEHOLDER = "[redacted]"

# Environment variable names whose *values* must never reach a log file. Matched
# against the name, so a new provider added to .env is covered without a change
# here as long as it is named like a credential.
SECRET_NAME_PATTERN = re.compile(
    r"(API[_-]?KEY|SECRET|TOKEN|PASSWORD|PASSWD|CREDENTIAL|PRIVATE[_-]?KEY|SESSION|COOKIE|CRUMB)",
    re.IGNORECASE,
)

# Below this length a value is not a credential, and blanket-replacing a short
# string would corrupt the log. FORECASTER_COST_CEILING_USD is two characters;
# replacing every "25" in the file would be its own kind of data loss.
MINIMUM_SECRET_LENGTH = 12

PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # Authorization and api-key headers, however they are spelled or quoted.
    (
        re.compile(
            r"""(?ix)
            \b(authorization|x[-_]api[-_]key|api[-_]?key|apikey|access[-_]?token|
               auth[-_]?token|bearer[-_]?token|x[-_]auth[-_]token)
            \b\s*["']?\s*[:=]\s*["']?
            ([^\s"',;}\]]{6,})
            """
        ),
        r"\1: " + PLACEHOLDER,
    ),
    # "Bearer <token>" appearing without a header name in front of it.
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9_\-\.=+/]{12,}"), "Bearer " + PLACEHOLDER),
    # Prefixed provider keys: sk-ant-..., sk-..., tvly-..., xai-..., pk_live_...
    (re.compile(r"\b(?:sk|pk|rk|tvly|xai|gsk|hf)[-_][A-Za-z0-9_\-]{12,}"), PLACEHOLDER),
    # JSON web tokens, which carry claims in the clear and are still credentials.
    (re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}"), PLACEHOLDER),
    # A key smuggled through a URL query string.
    (
        re.compile(r"(?i)([?&](?:api[-_]?key|key|token|access[-_]?token)=)[^&\s\"']{6,}"),
        r"\1" + PLACEHOLDER,
    ),
)


def secret_values(environ: dict[str, str] | None = None) -> list[str]:
    """Every environment value long enough to be a credential and named like one.

    Sorted longest first so that a key which contains another as a prefix is
    replaced whole rather than leaving its tail behind.
    """
    environ = os.environ if environ is None else environ
    values = {
        value
        for name, value in environ.items()
        if SECRET_NAME_PATTERN.search(name) and len(value.strip()) >= MINIMUM_SECRET_LENGTH
    }
    return sorted((value.strip() for value in values), key=len, reverse=True)


class Redactor:
    """Holds the environment snapshot so the scan is not repeated per line."""

    def __init__(self, environ: dict[str, str] | None = None) -> None:
        self._values = secret_values(environ)

    def __call__(self, text: str) -> str:
        return self.scrub(text)

    def scrub(self, text: str) -> str:
        if not text:
            return text
        for value in self._values:
            if value in text:
                text = text.replace(value, PLACEHOLDER)
        for pattern, replacement in PATTERNS:
            text = pattern.sub(replacement, text)
        return text


def scrub(text: str, environ: dict[str, str] | None = None) -> str:
    """One-shot convenience wrapper. Prefer Redactor when scrubbing many lines."""
    return Redactor(environ).scrub(text)
