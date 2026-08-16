"""J2, market data: analyst consensus, with the count, the spread and the
revision recency that lambda's regime conditions need.

Consensus is the single most valuable thing this source provides. It is lambda's
second input, it is what the baseline is built from, and under this event's
relative scoring it is the number our error is measured against. An adapter
returning a bare float would be useless here.

Yahoo's quoteSummary endpoint needs a cookie and crumb before it will answer.
The handshake is done once per process and the crumb reused.

One hard limitation, enforced rather than documented. This endpoint serves
*current* consensus only; there is no as-of history. For this run that is
point-in-time correct, because as_of is the lock date and all four companies
report after it -- the quarter has ended and has not been reported, which is
exactly the bar the company is about to face. But a backtest asking for a past
quarter would silently receive today's consensus, which is lookahead of the
worst kind: it would show up as skill. So the adapter *refuses* a historical
as_of rather than answering it. A leaking source is a bug to fix, not a
transient failure to fall back from.
"""

from __future__ import annotations

import http.cookiejar
import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone
from typing import Any

from .loader import SourceUnavailable

BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
COOKIE_URL = "https://fc.yahoo.com"
CRUMB_URL = "https://query1.finance.yahoo.com/v1/test/getcrumb"
SUMMARY_URL = "https://query2.finance.yahoo.com/v10/finance/quoteSummary/{symbol}"
MODULES = "earningsTrend,defaultKeyStatistics,price"

TIMEOUT_S = 25

# Our tickers are the organisers'. Yahoo wants its own symbols.
SYMBOLS = {
    "HD": "HD",
    "ADI": "ADI",
    "DE": "DE",
    "LSE:HAS": "HAS.L",
}


class PointInTimeUnavailable(Exception):
    """This source cannot answer for a date other than the lock date."""


class YahooMarketSource:
    """Priority-two source. The corpus answers filings and transcripts; this
    answers what the corpus has never contained."""

    name = "market"

    def __init__(self, settings: dict[str, Any] | None = None) -> None:
        settings = settings or {}
        self.symbols: dict[str, str] = settings.get("symbols") or dict(SYMBOLS)
        self._crumb: str | None = None
        self._opener: Any = None

    # -- protocol --------------------------------------------------------

    def consensus(self, ticker: str, period: str, as_of: date) -> dict[str, Any] | None:
        self._guard_as_of(as_of)
        symbol = self.symbols.get(ticker)
        if symbol is None:
            return None

        payload = self._quote_summary(symbol)
        if payload is None:
            return None

        trend = _dig(payload, "earningsTrend", "trend") or []
        row = next((item for item in trend if item.get("period") == "0q"), None)
        if row is None:
            return None

        earnings = row.get("earningsEstimate") or {}
        revenue = row.get("revenueEstimate") or {}
        revisions = row.get("epsRevisions") or {}

        eps_avg = _raw(earnings, "avg")
        analysts = _raw(earnings, "numberOfAnalysts")
        if eps_avg is None or not analysts:
            # Hays lands here: no coverage on this endpoint. Returning nothing
            # lets the chain fall through and the gap be recorded, which is the
            # honest outcome. Never synthesise a consensus.
            return None

        low, high = _raw(earnings, "low"), _raw(earnings, "high")
        return {
            "source": self.name,
            "symbol": symbol,
            "ticker": ticker,
            "requested_period": period,
            "period_end": row.get("endDate"),
            "eps": {
                "mean": eps_avg,
                "low": low,
                "high": high,
                "analysts": int(analysts),
                # Range-based, not a standard deviation: this endpoint publishes
                # the high and the low, never the dispersion itself. Named for
                # what it is so nobody reads it as a sigma.
                "dispersion_range_pct": _range_pct(low, high, eps_avg),
                "year_ago": _raw(earnings, "yearAgoEps"),
            },
            "revenue": {
                "mean": _raw(revenue, "avg"),
                "low": _raw(revenue, "low"),
                "high": _raw(revenue, "high"),
                "analysts": int(_raw(revenue, "numberOfAnalysts") or 0),
                "year_ago": _raw(revenue, "yearAgoRevenue"),
            },
            "revisions": {
                "up_7d": _raw(revisions, "upLast7days") or 0,
                "down_7d": _raw(revisions, "downLast7days") or 0,
                "up_30d": _raw(revisions, "upLast30days") or 0,
                "down_30d": _raw(revisions, "downLast30days") or 0,
            },
            "computed_at": as_of.isoformat(),
            "as_of_history_available": False,
            "note": (
                "current consensus as published on the lock date; this endpoint has no as-of "
                "history, so no historical quarter can be scored against the bar the Street "
                "actually set"
            ),
        }

    def shares(self, ticker: str, as_of: date) -> dict[str, Any] | None:
        self._guard_as_of(as_of)
        symbol = self.symbols.get(ticker)
        if symbol is None:
            return None
        payload = self._quote_summary(symbol)
        if payload is None:
            return None
        outstanding = _raw(_dig(payload, "defaultKeyStatistics") or {}, "sharesOutstanding")
        if outstanding is None:
            return None
        return {"source": self.name, "symbol": symbol, "shares_outstanding": outstanding}

    # Declared so the chain can fall through cleanly rather than skipping the
    # method entirely; this source does not answer them.
    def filings(self, ticker: str, as_of: date) -> None:
        return None

    def transcripts(self, ticker: str, as_of: date) -> None:
        return None

    def slides(self, ticker: str, as_of: date) -> None:
        return None

    def actuals(self, ticker: str, as_of: date) -> None:
        return None

    def peers(self, ticker: str, as_of: date) -> None:
        return None

    def fx(self, ticker: str, as_of: date) -> None:
        return None

    def macro(self, series: str, as_of: date) -> None:
        return None

    # -- internals -------------------------------------------------------

    def _guard_as_of(self, as_of: date) -> None:
        today = datetime.now(timezone.utc).date()
        if as_of < today:
            raise PointInTimeUnavailable(
                f"{self.name} publishes current consensus only and has no vintage for "
                f"{as_of.isoformat()}. Answering would return today's estimates for a past "
                "quarter, which is lookahead that shows up as skill."
            )

    def _handshake(self) -> str:
        if self._crumb:
            return self._crumb
        jar = http.cookiejar.CookieJar()
        self._opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        try:
            self._open(COOKIE_URL)
        except Exception:
            # The cookie endpoint answers 404 while still setting the cookie.
            pass
        try:
            crumb = self._open(CRUMB_URL).decode("utf-8").strip()
        except Exception as error:
            raise SourceUnavailable(f"{self.name}: crumb handshake failed: {error}") from error
        if not crumb or "<" in crumb:
            raise SourceUnavailable(f"{self.name}: crumb handshake returned {crumb[:40]!r}")
        self._crumb = crumb
        return crumb

    def _open(self, url: str) -> bytes:
        request = urllib.request.Request(url, headers={"User-Agent": BROWSER_UA})
        opener = self._opener or urllib.request.build_opener()
        return opener.open(request, timeout=TIMEOUT_S).read()

    def _quote_summary(self, symbol: str) -> dict[str, Any] | None:
        crumb = self._handshake()
        url = (
            SUMMARY_URL.format(symbol=urllib.parse.quote(symbol))
            + f"?modules={urllib.parse.quote(MODULES)}&crumb={urllib.parse.quote(crumb)}"
        )
        try:
            payload = json.loads(self._open(url))
        except urllib.error.HTTPError as error:
            if error.code == 404:
                return None
            raise SourceUnavailable(f"{self.name}: HTTP {error.code} for {symbol}") from error
        except (urllib.error.URLError, TimeoutError) as error:
            raise SourceUnavailable(f"{self.name}: {error}") from error

        results = _dig(payload, "quoteSummary", "result")
        if not results:
            return None
        return results[0]


def _dig(payload: dict[str, Any], *keys: str) -> Any:
    current: Any = payload
    for key in keys:
        if not isinstance(current, dict) or key not in current:
            return None
        current = current[key]
    return current


def _raw(block: dict[str, Any], key: str) -> Any:
    value = block.get(key)
    if isinstance(value, dict):
        return value.get("raw")
    return value


def _range_pct(low: Any, high: Any, mean: Any) -> float | None:
    if low is None or high is None or not mean:
        return None
    return round(abs(high - low) / abs(mean), 4)
