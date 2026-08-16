"""The frozen local corpus adapter: 1,139 markdown documents, no network.

This is priority-one and, for this event, the only source that answers anything.
It holds filings, call transcripts and slides for the four target companies. It
holds no analyst consensus, no price history and no macro series, so those
methods return None and the loader records the gap rather than papering over it.

The index is built from the first few kilobytes of each file -- enough for the
frontmatter block and the title heading -- because the corpus is 102 MB and a
stage that wants the last eight transcripts should not read all of it.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from ..config import REPO_ROOT
from ..documents import CALL_TRANSCRIPT, FILING, SLIDE, Document
from .protocol import guard_point_in_time

HEAD_BYTES = 8192

SUBDIRECTORIES = {
    FILING: "filings",
    CALL_TRANSCRIPT: "call-transcripts",
    SLIDE: "slides",
}


class CorpusSource:
    name = "corpus"

    def __init__(self, settings: dict, root: Path | None = None) -> None:
        self.root = (root or REPO_ROOT) / settings["root"]
        self.company_directories: dict[str, str] = settings["company_directories"]
        self._index: dict[str, list[Document]] = {}

    # -- protocol ---------------------------------------------------------

    def filings(self, ticker: str, as_of: date) -> list[Document] | None:
        return self._of_type(ticker, FILING, as_of)

    def transcripts(self, ticker: str, as_of: date) -> list[Document] | None:
        return self._of_type(ticker, CALL_TRANSCRIPT, as_of)

    def slides(self, ticker: str, as_of: date) -> list[Document] | None:
        return self._of_type(ticker, SLIDE, as_of)

    def consensus(self, ticker: str, period: str, as_of: date) -> dict | None:
        return None

    def actuals(self, ticker: str, as_of: date) -> dict | None:
        return None

    def prices(self, ticker: str, as_of: date) -> list | None:
        return None

    def peers(self, ticker: str, as_of: date) -> list[str] | None:
        return None

    def fx(self, ticker: str, as_of: date) -> dict | None:
        return None

    def macro(self, series: str, as_of: date) -> list | None:
        return None

    # -- internals --------------------------------------------------------

    def _of_type(self, ticker: str, doc_type: str, as_of: date) -> list[Document] | None:
        if ticker not in self.company_directories:
            return None
        documents = [doc for doc in self._documents_for(ticker) if doc.doc_type == doc_type]
        visible = [doc for doc in documents if doc.published_at <= as_of]
        if not visible:
            return None
        return guard_point_in_time(visible, as_of, source=self.name, method=doc_type.lower())

    def _documents_for(self, ticker: str) -> list[Document]:
        if ticker in self._index:
            return self._index[ticker]

        company_dir = self.root / self.company_directories[ticker]
        documents: list[Document] = []
        for doc_type, subdirectory in SUBDIRECTORIES.items():
            folder = company_dir / subdirectory
            if not folder.is_dir():
                continue
            for path in sorted(folder.glob("*.md")):
                document = self._read_head(path, doc_type)
                if document is not None:
                    documents.append(document)

        documents.sort(key=lambda doc: (doc.published_at, doc.doc_id), reverse=True)
        self._index[ticker] = documents
        return documents

    def _read_head(self, path: Path, doc_type: str) -> Document | None:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            head = handle.read(HEAD_BYTES)

        from ..frontmatter import parse_frontmatter

        fields, body = parse_frontmatter(head, origin=str(path))
        published = fields.get("published_at")
        if published is None:
            raise ValueError(f"{path}: no published_at in frontmatter; the point-in-time guard needs it")

        title = ""
        for line in body.split("\n"):
            if line.startswith("# "):
                title = line[2:].strip()
                break

        return Document(
            doc_id=path.stem,
            company=fields.get("company") or "",
            ticker=fields.get("ticker") or "",
            published_at=date.fromisoformat(published),
            doc_type=fields.get("document_type") or doc_type,
            period=fields.get("period"),
            title=title,
            source_url=fields.get("source_url"),
            path=path,
            source_name=self.name,
        )
