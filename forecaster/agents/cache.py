"""Content-hash disk cache.

The most useful thing in the client, because it is what makes it possible to
re-run the lenses fifty times against a fixed dossier without paying twice.

The prompt text is in the key, so editing a prompt invalidates its old answers
automatically. So is the model, the schema, and every request parameter that
could change the answer. A cache keyed on anything less returns yesterday's
answer to today's question and there is no symptom.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class ResponseCache:
    directory: Path
    hits: int = 0
    misses: int = 0

    def __post_init__(self) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def key(payload: dict[str, Any]) -> str:
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def path_for(self, key: str) -> Path:
        return self.directory / f"{key}.json"

    def get(self, key: str) -> dict[str, Any] | None:
        path = self.path_for(key)
        if not path.is_file():
            self.misses += 1
            return None
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            # A half-written cache entry is a miss, not a crash, and not a
            # silently truncated answer.
            self.misses += 1
            return None
        self.hits += 1
        return record

    def put(self, key: str, record: dict[str, Any]) -> None:
        path = self.path_for(key)
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(record, indent=2, default=str), encoding="utf-8")
        temporary.replace(path)

    def stats(self) -> dict[str, int]:
        return {"hits": self.hits, "misses": self.misses}
