"""The tier router.

Cheap for retrieval and extraction, mid for lenses and challenge, deep for
the judge alone.

The thinking and effort parameters are not supported on every model, and sending
them to one that lacks them is a hard 400 rather than a warning. It is the cheap
tier that lacks them -- Haiku 4.5 rejects `effort` outright and takes only the
older `thinking: {type: "enabled", budget_tokens: N}` form -- and the cheap tier
is what every extraction prompt runs on.

Gate on a model ID prefix match so dated snapshot IDs are covered, never on exact
model strings. `claude-haiku-4-5-20251001` must resolve the same way as
`claude-haiku-4-5`.
"""

from __future__ import annotations

from dataclasses import dataclass

CHEAP = "cheap"
MID = "mid"
DEEP = "deep"

TIERS = (CHEAP, MID, DEEP)

# Families that accept `thinking: {"type": "adaptive"}` and `output_config.effort`.
ADAPTIVE_THINKING_PREFIXES = (
    "claude-opus-5",
    "claude-sonnet-5",
    "claude-fable-5",
    "claude-opus-4-8",
    "claude-opus-4-7",
)

# Prompt-cache minimum prefix, in tokens, by family. A prefix shorter than this
# silently does not cache: no error, cache_creation_input_tokens just comes back
# zero. Haiku's 4096 is the one that bites, because the cheap tier is where the
# shared corpus is smallest.
CACHE_MINIMUM_TOKENS = {
    "claude-opus-5": 512,
    "claude-sonnet-5": 1024,
    "claude-haiku-4-5": 4096,
}


@dataclass(frozen=True)
class Tier:
    name: str
    model: str

    @property
    def supports_thinking(self) -> bool:
        return self.model.startswith(ADAPTIVE_THINKING_PREFIXES)

    @property
    def supports_effort(self) -> bool:
        # Same gate: effort and adaptive thinking arrived together and Haiku 4.5
        # has neither. Kept as a separate property so the reason a call omits
        # each one is readable at the call site.
        return self.supports_thinking

    @property
    def cache_minimum_tokens(self) -> int:
        for prefix, minimum in CACHE_MINIMUM_TOKENS.items():
            if self.model.startswith(prefix):
                return minimum
        return 4096


class TierRouter:
    def __init__(self, models: dict[str, str]) -> None:
        missing = [name for name in TIERS if name not in models]
        if missing:
            raise KeyError(f"config.models is missing tier(s): {', '.join(missing)}")
        self._tiers = {name: Tier(name=name, model=models[name]) for name in TIERS}

    def __getitem__(self, name: str) -> Tier:
        if name not in self._tiers:
            raise KeyError(f"unknown tier {name!r}; expected one of {', '.join(TIERS)}")
        return self._tiers[name]

    def roster(self) -> dict[str, str]:
        return {name: tier.model for name, tier in self._tiers.items()}
