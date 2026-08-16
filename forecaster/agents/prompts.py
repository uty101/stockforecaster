"""The prompt store.

Every prompt lives in a versioned file under llm/prompts/. No prompt text inline in
Python, not even a one-line system message. The version of every prompt used
goes into the results file, because these will be iterated on more than a
hundred times and a backtest number is worthless if nobody can say which prompt
produced it.

Never edit a prompt in place without bumping the version. The cache key includes
the rendered prompt text, so an edited prompt invalidates its own old answers
automatically -- but the results file records the version, and a version that
stopped matching its text is a lie in the one place that has to stay honest.

Format is frontmatter plus a markdown body rather than YAML. PyYAML cannot be
installed on this machine, and a hand-rolled YAML subset parser that silently
accepts malformed input is a worse trade than a format the corpus already uses.

Placeholders are {{double_braced}}. Single braces are left alone because prompt
bodies contain JSON examples, and str.format would choke on every one of them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from ..config import REPO_ROOT
from ..frontmatter import read_frontmatter

PLACEHOLDER = re.compile(r"\{\{([a-z0-9_]+)\}\}")


class PromptError(Exception):
    pass


@dataclass(frozen=True)
class Prompt:
    name: str
    version: str
    tier: str
    body: str
    path: Path

    def render(self, **values: object) -> str:
        wanted = set(PLACEHOLDER.findall(self.body))
        given = set(values)

        missing = wanted - given
        if missing:
            raise PromptError(
                f"{self.name} v{self.version}: no value supplied for {', '.join(sorted(missing))}"
            )
        unused = given - wanted
        if unused:
            raise PromptError(
                f"{self.name} v{self.version}: {', '.join(sorted(unused))} supplied but the prompt "
                "has no placeholder for it; a value that reaches no prompt is a value the agent "
                "never saw"
            )

        rendered = self.body
        for key, value in values.items():
            rendered = rendered.replace("{{" + key + "}}", str(value))
        return rendered


class PromptStore:
    def __init__(self, directory: Path | None = None) -> None:
        self.directory = directory or REPO_ROOT / "llm" / "prompts"
        self._cache: dict[str, Prompt] = {}

    def get(self, name: str) -> Prompt:
        if name in self._cache:
            return self._cache[name]

        path = self.directory / f"{name}.md"
        if not path.is_file():
            raise PromptError(f"no prompt file at {path}")

        fields, body = read_frontmatter(path)
        for key in ("name", "version", "tier"):
            if not fields.get(key):
                raise PromptError(f"{path}: frontmatter is missing {key}")
        if fields["name"] != name:
            raise PromptError(f"{path}: declares name {fields['name']!r} but is filed as {name!r}")

        prompt = Prompt(
            name=name,
            version=str(fields["version"]),
            tier=str(fields["tier"]),
            body=body.strip(),
            path=path,
        )
        self._cache[name] = prompt
        return prompt

    def versions(self) -> dict[str, str]:
        """Every prompt loaded during this run, for the results file."""
        return {name: prompt.version for name, prompt in sorted(self._cache.items())}
