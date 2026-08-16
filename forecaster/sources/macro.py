"""J6, macro: FRED series with the vintage pinned to the lock date.

Macro data feels like ambient background rather than a document, which is exactly
why it is easy to forget that it is revised. A series read today for a quarter
that ended in July returns the revised figure, not the one that was published at
the time -- and a backtest built on revised macro data measures a forecaster who
knew things nobody knew.

So every request pins `realtime_start` to the lock date. FRED then returns the
series as it stood on that date, revisions after it excluded. That is the same
point-in-time guard the filings get, applied to the one source where its absence
would be invisible.

Which series matter is per company and lives in config, because the exposures are
not interchangeable: Home Depot moves on housing turnover, Deere on farm income
and crop prices, Hays on employment.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from typing import Any

from .loader import SourceUnavailable

BASE = "https://api.stlouisfed.org/fred/series/observations"
META = "https://api.stlouisfed.org/fred/series"
TIMEOUT_S = 30


class MacroSource:
    name = "macro"

    def __init__(self, settings: dict[str, Any] | None = None) -> None:
        settings = settings or {}
        self.api_key: str = settings.get("api_key") or os.environ.get("FRED_API_KEY", "")
        self.series: dict[str, list[dict[str, str]]] = settings.get("series") or {}
        self.observations: int = int(settings.get("observations", 8))

    def macro(self, ticker: str, as_of: date) -> list[dict[str, Any]] | None:
        if not self.api_key:
            return None
        wanted = self.series.get(ticker)
        if not wanted:
            return None

        collected: list[dict[str, Any]] = []
        for entry in wanted:
            try:
                payload = self._fetch(entry["id"], as_of)
            except SourceUnavailable:
                continue
            rows = [
                {"date": row["date"], "value": float(row["value"])}
                for row in payload.get("observations", [])
                if row.get("value") not in (".", "", None)
            ][-self.observations :]
            if not rows:
                continue
            collected.append(
                {
                    "source": self.name,
                    "series_id": entry["id"],
                    "label": entry.get("label", entry["id"]),
                    "why_it_matters": entry.get("why", ""),
                    "vintage": as_of.isoformat(),
                    "observations": rows,
                    "latest": rows[-1],
                    "change_over_window": round(rows[-1]["value"] - rows[0]["value"], 4),
                }
            )
        return collected or None

    def _fetch(self, series_id: str, as_of: date) -> dict[str, Any]:
        query = urllib.parse.urlencode(
            {
                "series_id": series_id,
                "api_key": self.api_key,
                "file_type": "json",
                # The vintage lock. Without this the series comes back revised and
                # the point-in-time guarantee quietly stops being true.
                "realtime_start": as_of.isoformat(),
                "realtime_end": as_of.isoformat(),
                "observation_end": as_of.isoformat(),
                "sort_order": "asc",
            }
        )
        request = urllib.request.Request(
            f"{BASE}?{query}", headers={"User-Agent": "agents-vs-wall-street forecaster"}
        )
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as error:
            raise SourceUnavailable(f"{self.name}: HTTP {error.code} for {series_id}") from error
        except (urllib.error.URLError, TimeoutError) as error:
            raise SourceUnavailable(f"{self.name}: {error}") from error

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
