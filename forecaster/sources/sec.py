"""J1+, SEC XBRL: tagged facts rather than parsed prose.

This is the structured half of the verification split in Part 2. A value from
here is the tagged fact itself, rendered by this adapter from a typed response.
There is no prose to string match because no model was ever involved -- which is
why it is not a loophole in the provenance rule.

Point in time is enforced on the `filed` date carried by every fact, which is the
acceptance date. That also handles restatements for free: an original and its
later restatement are separate entries with different accession numbers and
different filed dates, so filtering on `filed <= as_of` returns the figure as it
stood, never the tidied-up version published afterwards.

Hays is not an SEC registrant. It returns nothing here and falls through to the
corpus, which is the correct answer rather than a failure.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path
from typing import Any

from .loader import SourceUnavailable

FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
TIMEOUT_S = 60

# The income-statement tags a P&L model needs. Several aliases per line because
# filers tag the same economics differently and the first one present wins.
LINE_TAGS: dict[str, tuple[str, ...]] = {
    "revenue": (
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "Revenues",
        "SalesRevenueNet",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
    ),
    "cost_of_revenue": ("CostOfGoodsAndServicesSold", "CostOfRevenue", "CostOfGoodsSold"),
    "gross_profit": ("GrossProfit",),
    "sga": ("SellingGeneralAndAdministrativeExpense", "GeneralAndAdministrativeExpense"),
    "rnd": ("ResearchAndDevelopmentExpense",),
    "depreciation": ("DepreciationDepletionAndAmortization", "DepreciationAndAmortization"),
    "operating_income": ("OperatingIncomeLoss",),
    "pretax_income": (
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments",
    ),
    "tax_expense": ("IncomeTaxExpenseBenefit",),
    "net_income": ("NetIncomeLoss",),
    "eps_diluted": ("EarningsPerShareDiluted",),
    "eps_basic": ("EarningsPerShareBasic",),
    "diluted_shares": ("WeightedAverageNumberOfDilutedSharesOutstanding",),
}

PERIODIC_FORMS = ("10-Q", "10-K")


class SecSource:
    name = "sec"

    def __init__(self, settings: dict[str, Any] | None = None) -> None:
        settings = settings or {}
        self.identity: str = (
            settings.get("identity")
            or os.environ.get("SEC_IDENTITY")
            or "agents-vs-wall-street forecaster"
        )
        self.ciks: dict[str, int] = {k: int(v) for k, v in (settings.get("ciks") or {}).items()}
        self.cache_dir = Path(settings.get("cache_dir") or ".forecaster-cache/sec")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._facts: dict[str, dict[str, Any]] = {}

    # -- protocol --------------------------------------------------------

    def history(self, ticker: str, as_of: date) -> dict[str, Any] | None:
        """Income-statement series, as filed, nothing accepted after as_of."""
        facts = self._company_facts(ticker)
        if facts is None:
            return None

        us_gaap = (facts.get("facts") or {}).get("us-gaap") or {}
        series: dict[str, list[dict[str, Any]]] = {}
        tags_used: dict[str, str] = {}

        for line, aliases in LINE_TAGS.items():
            for tag in aliases:
                observations = _observations(us_gaap.get(tag), as_of)
                if observations:
                    series[line] = observations
                    tags_used[line] = tag
                    break

        if not series.get("revenue"):
            return None

        return {
            "source": self.name,
            "ticker": ticker,
            "cik": self.ciks.get(ticker),
            "entity": facts.get("entityName"),
            "as_of": as_of.isoformat(),
            "tags_used": tags_used,
            "series": series,
        }

    def peers(self, ticker: str, as_of: date) -> list[str] | None:
        """SIC-coded peers. The submissions endpoint carries the filer's SIC."""
        submissions = self._submissions(ticker)
        if submissions is None:
            return None
        sic = submissions.get("sic")
        return [f"SIC:{sic}"] if sic else None

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

    def fx(self, ticker: str, as_of: date) -> None:
        return None

    def macro(self, series: str, as_of: date) -> None:
        return None

    # -- internals -------------------------------------------------------

    def _company_facts(self, ticker: str) -> dict[str, Any] | None:
        if ticker in self._facts:
            return self._facts[ticker]
        cik = self.ciks.get(ticker)
        if cik is None:
            return None
        payload = self._get_json(FACTS_URL.format(cik=cik), f"facts-{cik}.json")
        self._facts[ticker] = payload
        return payload

    def _submissions(self, ticker: str) -> dict[str, Any] | None:
        cik = self.ciks.get(ticker)
        if cik is None:
            return None
        return self._get_json(SUBMISSIONS_URL.format(cik=cik), f"submissions-{cik}.json")

    def _get_json(self, url: str, filename: str) -> dict[str, Any] | None:
        """Cached on disk. The corpus is frozen and these facts are historical,
        so a second run costs nothing and the network is touched once."""
        path = self.cache_dir / filename
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))

        request = urllib.request.Request(
            url, headers={"User-Agent": self.identity, "Accept-Encoding": "gzip, deflate"}
        )
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
                raw = response.read()
                if response.headers.get("Content-Encoding") == "gzip":
                    import gzip

                    raw = gzip.decompress(raw)
        except urllib.error.HTTPError as error:
            if error.code == 404:
                return None
            raise SourceUnavailable(f"{self.name}: HTTP {error.code} for {url}") from error
        except (urllib.error.URLError, TimeoutError) as error:
            raise SourceUnavailable(f"{self.name}: {error}") from error

        text = raw.decode("utf-8")
        path.write_text(text, encoding="utf-8")
        return json.loads(text)


def _observations(concept: dict[str, Any] | None, as_of: date) -> list[dict[str, Any]]:
    """Every reported value for one tag, as filed, deduplicated by period.

    Where a period has been reported more than once -- an original and a later
    restatement -- the earliest filing wins, because the point-in-time invariant
    says a restated figure must never be substituted into a period whose
    original filing predates it. The restatement is kept alongside so the
    Integrity sheet can show that the choice was made rather than missed.
    """
    if not concept:
        return []

    chosen: dict[tuple[str, str], dict[str, Any]] = {}
    for unit_values in (concept.get("units") or {}).values():
        for entry in unit_values:
            filed = entry.get("filed")
            if not filed or date.fromisoformat(filed) > as_of:
                continue
            if entry.get("form") not in PERIODIC_FORMS:
                continue
            key = (entry.get("start") or "", entry.get("end") or "")
            record = {
                "start": entry.get("start"),
                "end": entry.get("end"),
                "value": entry.get("val"),
                "form": entry.get("form"),
                "filed": filed,
                "accession": entry.get("accn"),
                "fiscal_year": entry.get("fy"),
                "fiscal_period": entry.get("fp"),
                "restatements_seen": 0,
            }
            existing = chosen.get(key)
            if existing is None:
                chosen[key] = record
            elif filed < existing["filed"]:
                record["restatements_seen"] = existing["restatements_seen"] + 1
                chosen[key] = record
            else:
                existing["restatements_seen"] += 1

    return sorted(chosen.values(), key=lambda item: (item["end"] or "", item["start"] or ""))
