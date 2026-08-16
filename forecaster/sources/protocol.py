"""The one protocol every source implements.

Every method may return None, meaning "this source does not have it", and the
loader falls through to the next source by priority. A half-working adapter is
still useful, so a missing method is not an error.

Every method takes the as-of date and refuses anything filed later. The check
lives inside the method, not at the call site, because a caller who forgets is
exactly the bug the guard exists to catch. The loader then re-checks everything
that comes back, so an adapter that forgets is caught too.

Methods the specification lists that no source here answers -- consensus,
prices, fx, macro, peers, news -- are kept on the protocol and return None. The
fall-through events they produce are the honest record, on the Integrity sheet,
that this system has no analyst consensus and no market data.
"""

from __future__ import annotations

from datetime import date
from typing import Protocol, runtime_checkable

from ..documents import Document


class PointInTimeViolation(Exception):
    """Raised when a source returns something filed after the as-of date."""


@runtime_checkable
class Source(Protocol):
    name: str

    def filings(self, ticker: str, as_of: date) -> list[Document] | None: ...

    def transcripts(self, ticker: str, as_of: date) -> list[Document] | None: ...

    def slides(self, ticker: str, as_of: date) -> list[Document] | None: ...

    def consensus(self, ticker: str, period: str, as_of: date) -> dict | None: ...

    def actuals(self, ticker: str, as_of: date) -> dict | None: ...

    def prices(self, ticker: str, as_of: date) -> list | None: ...

    def peers(self, ticker: str, as_of: date) -> list[str] | None: ...

    def fx(self, ticker: str, as_of: date) -> dict | None: ...

    def macro(self, series: str, as_of: date) -> list | None: ...


# Methods the pipeline cannot run without. Stage A raises, naming the method, if
# every source in the chain returns nothing for one of these.
REQUIRED_METHODS = ("filings", "transcripts")

OPTIONAL_METHODS = ("slides", "consensus", "actuals", "prices", "peers", "fx", "macro")


def guard_point_in_time(documents: list[Document], as_of: date, *, source: str, method: str) -> list[Document]:
    late = [doc for doc in documents if doc.published_at > as_of]
    if late:
        raise PointInTimeViolation(
            f"{source}.{method} returned {len(late)} document(s) published after {as_of.isoformat()}: "
            + ", ".join(f"{doc.doc_id} ({doc.published_at.isoformat()})" for doc in late[:5])
        )
    return documents
