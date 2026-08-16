"""The priority chain: ask each source in turn, record who answered.

When a source returns nothing and the chain falls through, an event names the
source, the method and the fallback used. Across a run those events say which
adapter is actually carrying the work, which is rarely the one you would guess,
and they are what the Integrity sheet renders.

Everything a source hands back is re-checked against the as-of date here. The
adapter checks too; this is the second lock, and it is the one that catches an
adapter written in twenty minutes by somebody who forgot.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from ..config import Config
from ..documents import Document
from ..events import EventSink
from .corpus import CorpusSource
from .protocol import OPTIONAL_METHODS, REQUIRED_METHODS, Source, guard_point_in_time

STAGE = "A"

# Adapters raise this to mean "I could not reach my data", which is a
# fall-through. Anything else propagates, because a KeyError in an adapter is a
# bug and swallowing it would turn it into a silent missing source.
class SourceUnavailable(Exception):
    pass


NETWORK_SHAPED = (SourceUnavailable, TimeoutError, ConnectionError)

def _builders() -> dict:
    # Imported lazily: the network adapters import urllib, and a corpus-only
    # run should not pay for that or fail on it.
    from .market import YahooMarketSource
    from .news import NewsSource
    from .sec import SecSource

    return {
        "corpus": CorpusSource,
        "sec": SecSource,
        "market": YahooMarketSource,
        "news": NewsSource,
    }


class MissingRequiredData(Exception):
    """Raised when every source in the chain returns nothing for a required method."""


class SourceChain:
    def __init__(self, sources: list[Source], events: EventSink, as_of: date) -> None:
        self.sources = sources
        self.events = events
        self.as_of = as_of
        self.answered_by: dict[str, str | None] = {}
        self.fallthroughs: list[dict[str, Any]] = []

    @property
    def names(self) -> list[str]:
        return [source.name for source in self.sources]

    def fetch(self, method: str, *args: Any, required: bool = False) -> Any:
        request = f"{method}({', '.join(str(arg) for arg in args)})"
        tried: list[str] = []

        for source in self.sources:
            handler = getattr(source, method, None)
            if handler is None:
                self._fell_through(source.name, method, request, "method not implemented")
                tried.append(source.name)
                continue
            try:
                value = handler(*args, self.as_of)
            except NETWORK_SHAPED as error:
                self._fell_through(source.name, method, request, f"unavailable: {error}")
                tried.append(source.name)
                continue

            if value is None:
                self._fell_through(source.name, method, request, "returned nothing")
                tried.append(source.name)
                continue

            if isinstance(value, list) and value and isinstance(value[0], Document):
                guard_point_in_time(value, self.as_of, source=source.name, method=method)

            self.answered_by[request] = source.name
            self.events.emit(
                STAGE,
                "source_answered",
                request=request,
                source=source.name,
                items=len(value) if isinstance(value, list) else 1,
                fell_through=tried,
            )
            return value

        self.answered_by[request] = None
        self.events.emit(STAGE, "source_exhausted", request=request, tried=tried)
        if required:
            raise MissingRequiredData(
                f"no source answered {request}; tried {', '.join(tried) or 'nothing'}. "
                f"Required methods are {', '.join(REQUIRED_METHODS)}."
            )
        return None

    def _fell_through(self, source: str, method: str, request: str, reason: str) -> None:
        record = {"source": source, "method": method, "request": request, "reason": reason}
        self.fallthroughs.append(record)
        self.events.emit(STAGE, "source_fell_through", **record)

    def integrity_record(self) -> dict[str, Any]:
        answered_methods = {
            _method_of(request) for request, source in self.answered_by.items() if source is not None
        }
        attempted_methods = {_method_of(request) for request in self.answered_by}
        return {
            "priority": self.names,
            "answered_by": dict(self.answered_by),
            "fell_through": list(self.fallthroughs),
            "unanswered": sorted(k for k, v in self.answered_by.items() if v is None),
            # A method every source declined is a capability this system does not
            # have. Named here rather than inferred from silence downstream.
            "methods_no_source_answered": sorted(attempted_methods - answered_methods),
        }


def _method_of(request: str) -> str:
    return request.split("(", 1)[0]


def build_chain(config: Config, events: EventSink) -> SourceChain:
    sources: list[Source] = []
    for name in config.source_priority:
        settings = config.source(name)
        builder = _builders().get(name)
        if builder is None:
            raise KeyError(f"config lists source {name!r} but no adapter is registered for it")
        sources.append(builder(settings))
    return SourceChain(sources, events, config.as_of)
