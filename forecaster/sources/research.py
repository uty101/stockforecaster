"""U5, industry research: web retrieval that becomes citable evidence.

The corpus tells us what the company said about itself. It says nothing about the
industry the company sits in, and for these four names the industry is where the
revenue and cost lines actually come from -- US housing turnover and repair-and-
remodel spending for Home Depot, the analog semiconductor cycle for ADI, farm
income and crop prices for Deere, the permanent recruitment market for Hays.

Two things make this evidence rather than a hint.

Full text is retrieved and written to disk as a document, so a lens that cites it
is quoting something that exists and V1 can string-match the quote the same way
it matches a quote from an 8-K. A retrieved snippet could not be verified that
way, and an unverifiable quote is exactly what the provenance invariant exists to
stop.

The window is bounded at the API. Nothing published after the lock date can enter
the result set, so an article written the day after the print -- which for these
companies would be an article about the answer -- cannot reach a lens.

This is deliberately not the perception path. Perception reads how the stock is
being written about and is firewalled from every revenue and margin driver.
Industry research reads what is happening in the market the company sells into,
and feeding that to the Demand, Market and Macro lenses is the entire reason
those lenses exist.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from datetime import date, timedelta
from typing import Any

from .loader import SourceUnavailable

SEARCH_URL = "https://api.exa.ai/search"
TIMEOUT_S = 45
DEFAULT_LOOKBACK_DAYS = 180
DEFAULT_RESULTS = 4
MAX_CHARS = 12_000


class WebResearchSource:
    name = "research"

    def __init__(self, settings: dict[str, Any] | None = None) -> None:
        settings = settings or {}
        self.api_key: str = settings.get("api_key") or os.environ.get("EXA_API_KEY", "")
        self.lookback_days: int = int(settings.get("lookback_days", DEFAULT_LOOKBACK_DAYS))
        self.max_results: int = int(settings.get("max_results", DEFAULT_RESULTS))
        self.max_chars: int = int(settings.get("max_characters", MAX_CHARS))
        # Two angles per company on purpose: what drives the top line, and what
        # drives the cost line. A single "outlook" query returns commentary about
        # the share price, which is the other path's job.
        self.queries: dict[str, list[dict[str, str]]] = settings.get("queries") or {}

    def industry(self, ticker: str, as_of: date) -> list[dict[str, Any]] | None:
        if not self.api_key:
            return None
        angles = self.queries.get(ticker)
        if not angles:
            return None

        start = as_of - timedelta(days=self.lookback_days)
        collected: list[dict[str, Any]] = []
        seen_urls: set[str] = set()

        for angle in angles:
            payload = self._search(angle["query"], start, as_of)
            for entry in payload.get("results", []):
                url = entry.get("url") or ""
                published = (entry.get("publishedDate") or "")[:10]
                text = entry.get("text") or ""
                if not url or url in seen_urls or not published or not text.strip():
                    # No date means no point-in-time check, and no text means
                    # nothing a quote could ever be matched against. Neither gets
                    # to be evidence.
                    continue
                if date.fromisoformat(published) > as_of:
                    continue
                seen_urls.add(url)
                collected.append(
                    {
                        "source": self.name,
                        "angle": angle["angle"],
                        "title": (entry.get("title") or "Untitled").strip(),
                        "url": url,
                        "published_at": published,
                        "text": text[: self.max_chars],
                        "slug": _slug(published, entry.get("title") or url),
                    }
                )
        return collected or None

    # -- internals -------------------------------------------------------

    def _search(self, query: str, start: date, end: date) -> dict[str, Any]:
        body = json.dumps(
            {
                "query": query,
                "numResults": self.max_results,
                "type": "auto",
                "startPublishedDate": f"{start.isoformat()}T00:00:00.000Z",
                "endPublishedDate": f"{end.isoformat()}T00:00:00.000Z",
                "contents": {"text": {"maxCharacters": self.max_chars}},
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            SEARCH_URL,
            data=body,
            method="POST",
            headers={
                "x-api-key": self.api_key,
                "content-type": "application/json",
                "User-Agent": "agents-vs-wall-street forecaster",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as error:
            raise SourceUnavailable(f"{self.name}: HTTP {error.code}") from error
        except (urllib.error.URLError, TimeoutError) as error:
            raise SourceUnavailable(f"{self.name}: {error}") from error

    # The rest of the protocol: this source answers industry research and
    # nothing else, so the chain falls through cleanly.
    def filings(self, ticker: str, as_of: date) -> None:
        return None

    def transcripts(self, ticker: str, as_of: date) -> None:
        return None

    def slides(self, ticker: str, as_of: date) -> None:
        return None

    def consensus(self, ticker: str, period: str, as_of: date) -> None:
        return None

    def actuals(self, ticker: str, as_of: date) -> None:
        return None

    def peers(self, ticker: str, as_of: date) -> None:
        return None

    def fx(self, ticker: str, as_of: date) -> None:
        return None

    def macro(self, series: str, as_of: date) -> None:
        return None


def _slug(published: str, title: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:60]
    return f"{published}__{cleaned or 'untitled'}"
