"""The fan-out runner.

Prompt caching is a prefix match and there are two ways to defeat it, neither of
which announces itself.

The shared corpus must come before the per-lens system text when messages are
built. If the lens-specific text leads, every lens presents a different prefix
and there is nothing to match. That ordering is enforced in build_system_blocks.

The first lens must finish before the other eight start. A cold parallel fan-out
means all nine call before any cache entry exists, so all nine write and none
read. So: a warm-up call, then the fan-out, and the latency is accepted.

This is the only concurrency in the system.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Iterable, Sequence

from ..events import EventSink
from .client import cacheable, plain
from .tiers import Tier

STAGE_DEFAULT = "E3"

# Everything an agent writes can end up rendered on a sheet, and the sheets do
# not repair the text they are given. So the house punctuation rule is stated
# once, here, rather than in nineteen prompt files that can drift apart.
HOUSE_STYLE = """
Punctuation, in every string you return: no dash may join two parts of a
sentence. No em dash, no en dash, no hyphen with a space beside it. Where a
clause needs separating, use a full stop and start a new sentence. Where a clause
is subordinate, use a comma. Write a range with the word to, as in 2.35 to 2.45.
Hyphens inside a standard compound word are fine.
""".strip()


def build_system_blocks(shared_corpus: str, own_question: str, tier: Tier) -> list[dict[str, Any]]:
    """Shared corpus first, always. The per-agent text can never lead."""
    return [cacheable(shared_corpus, tier), plain(f"{own_question}\n\n{HOUSE_STYLE}")]


def fan_out(
    items: Sequence[Any],
    worker: Callable[[Any], Any],
    *,
    events: EventSink,
    stage: str = STAGE_DEFAULT,
    label: Callable[[Any], str] = str,
    max_workers: int = 8,
) -> list[Any]:
    """Warm up on the first item, then run the rest concurrently.

    Exceptions are returned in place rather than raised, so one lens failing does
    not take the other eight with it. The caller decides what a failure means --
    which for lenses is V1's job, and V1 is deterministic on purpose.
    """
    if not items:
        return []

    results: list[Any] = [None] * len(items)

    events.emit(stage, "fan_out_warm_up", item=label(items[0]), total=len(items))
    results[0] = _guarded(worker, items[0])
    events.emit(stage, "fan_out_warmed", item=label(items[0]))

    remaining = list(enumerate(items))[1:]
    if remaining:
        events.emit(stage, "fan_out_started", count=len(remaining), max_workers=max_workers)
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {pool.submit(_guarded, worker, item): index for index, item in remaining}
            for future in futures:
                results[futures[future]] = future.result()
        events.emit(stage, "fan_out_finished", count=len(remaining))

    return results


def _guarded(worker: Callable[[Any], Any], item: Any) -> Any:
    try:
        return worker(item)
    except Exception as error:  # noqa: BLE001 - deliberately returned, not swallowed
        return error


def failures(results: Iterable[Any]) -> list[Exception]:
    return [result for result in results if isinstance(result, Exception)]


def successes(results: Iterable[Any]) -> list[Any]:
    return [result for result in results if not isinstance(result, Exception)]
