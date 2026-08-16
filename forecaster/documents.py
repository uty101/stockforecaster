"""The document record every source returns and every citation points back to.

Text is read lazily. There are 1,139 documents in the corpus and a stage that
only needs the last eight transcripts should not pay to read the other 1,131.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

FILING = "FILING"
CALL_TRANSCRIPT = "CALL_TRANSCRIPT"
SLIDE = "SLIDE"
# Retrieved from the open web and written to disk so a quote against it can be
# string-matched exactly like a quote against a filing.
RESEARCH = "RESEARCH"


@dataclass(frozen=True)
class Document:
    doc_id: str
    company: str
    ticker: str
    published_at: date
    doc_type: str
    period: str | None
    title: str
    source_url: str | None
    path: Path
    source_name: str

    def text(self) -> str:
        return self.path.read_text(encoding="utf-8", errors="replace")

    def contains(self, quote: str) -> bool:
        """Exact string match, whitespace-normalised.

        Normalising whitespace is the only latitude given. A quote assembled from
        two sentences, or paraphrased, must fail here — that is the entire point
        of the check at V1.
        """
        return normalise(quote) in normalise(self.text())

    def summary(self) -> dict[str, object]:
        return {
            "doc_id": self.doc_id,
            "published_at": self.published_at.isoformat(),
            "doc_type": self.doc_type,
            "period": self.period,
            "title": self.title,
            "source_url": self.source_url,
            "source_name": self.source_name,
        }


def normalise(text: str) -> str:
    return " ".join(text.split())
