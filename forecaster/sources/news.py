"""J3, news: date-bounded web retrieval.

The corpus holds filings, transcripts and slides. It holds no coverage at all, so
anything about how the name is being written about before the print has to come
from here.

Point in time is enforced at the API rather than after the fact. Exa accepts
startPublishedDate and endPublishedDate, so the upper bound is the lock date and
nothing published later can enter the result set in the first place. That is a
stronger guarantee than filtering a response we already hold, and it is the
reason this adapter is worth having rather than a general web scrape: an
unbounded search returns tomorrow's coverage of an event we are supposed to be
forecasting.

What this is not: a way to find the answer. Coverage published in the days before
a print is overwhelmingly preview and positioning, and treating it as evidence of
the outcome is how a forecast ends up restating the loudest recent headline.
It feeds perception only, and perception reaches no revenue or margin driver.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from datetime import date, timedelta
from typing import Any

from .loader import SourceUnavailable

SEARCH_URL = "https://api.exa.ai/search"
TIMEOUT_S = 30
DEFAULT_LOOKBACK_DAYS = 75
DEFAULT_RESULTS = 8


class NewsSource:
    name = "news"

    def __init__(self, settings: dict[str, Any] | None = None) -> None:
        settings = settings or {}
        self.api_key: str = settings.get("api_key") or os.environ.get("EXA_API_KEY", "")
        self.lookback_days: int = int(settings.get("lookback_days", DEFAULT_LOOKBACK_DAYS))
        self.max_results: int = int(settings.get("max_results", DEFAULT_RESULTS))
        self.queries: dict[str, str] = settings.get("queries") or {}

    # -- protocol --------------------------------------------------------

    def news(self, ticker: str, as_of: date) -> list[dict[str, Any]] | None:
        if not self.api_key:
            return None

        query = self.queries.get(ticker)
        if query is None:
            return None

        start = as_of - timedelta(days=self.lookback_days)
        body = json.dumps(
            {
                "query": query,
                "numResults": self.max_results,
                # The upper bound is the lock date. Nothing published after it can
                # enter the result set, so there is no post-hoc filter to forget.
                "startPublishedDate": f"{start.isoformat()}T00:00:00.000Z",
                "endPublishedDate": f"{as_of.isoformat()}T00:00:00.000Z",
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
                payload = json.loads(response.read())
        except urllib.error.HTTPError as error:
            raise SourceUnavailable(f"{self.name}: HTTP {error.code}") from error
        except (urllib.error.URLError, TimeoutError) as error:
            raise SourceUnavailable(f"{self.name}: {error}") from error

        items = []
        for entry in payload.get("results", []):
            published = (entry.get("publishedDate") or "")[:10]
            if not published:
                # No date means it cannot be point-in-time checked, so it does
                # not get to be evidence.
                continue
            if date.fromisoformat(published) > as_of:
                continue
            items.append(
                {
                    "source": self.name,
                    "title": entry.get("title") or "",
                    "url": entry.get("url") or "",
                    "published_at": published,
                    "author": entry.get("author"),
                    "snippet": (entry.get("text") or "")[:1200],
                }
            )
        return items or None

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
