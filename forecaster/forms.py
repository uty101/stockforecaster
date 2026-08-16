"""Classify a corpus document by its form, from the filename stem.

This is the item-code filter. The specification says to filter 8-Ks by item code
rather than taking the most recent few by date, because a large filer publishes
many 8-Ks a quarter covering director changes, shareholder votes and debt
offerings. This corpus has no item codes, but the stem carries the form:

    2026-07-10__has-ln-20260710-q4-8k__1572805      -> 8-K,  Q4
    2026-05-28__de-us-20260528-q2-10q__1055932      -> 10-Q, Q2
    2025-11-26__de-us-20251126-q4-10k__469216       -> 10-K, Q4
    2026-08-03__has-ln-20260803-filing__1600192     -> OTHER

Hays is the case that proves the point. On 2026-07-10 it filed its fourth
quarter trading statement, and the four filings immediately above it in date
order are voting-rights and own-share notifications. Taking the three most
recent by date misses the only one that matters.

The document title is deliberately not used. Titles are prose and the corpus
index also carries a `period` field that is wrong often enough to matter -- the
ADI 10-Q published 2026-05-20 is labelled Q3 2026 and is the Q2 report.
"""

from __future__ import annotations

import re

from .documents import CALL_TRANSCRIPT, FILING, SLIDE, Document

EIGHT_K = "8-K"
TEN_Q = "10-Q"
TEN_K = "10-K"
OTHER = "OTHER"

EARNINGS_CALL = "EARNINGS_CALL"
CONFERENCE = "CONFERENCE"
SHAREHOLDER_MEETING = "SHAREHOLDER_MEETING"

_FORM_TOKENS = ((r"-10k(?:-|_|$)", TEN_K), (r"-10q(?:-|_|$)", TEN_Q), (r"-8k(?:-|_|$)", EIGHT_K))

# h1 and h2 are Hays. A UK filer reports halves, files no 10-Q and no 10-K, and
# its full-year statements arrive inside the H2 results announcement. A tag
# pattern that only knows q1..q4 and fy silently classifies every Hays results
# release as an untagged miscellaneous filing.
_PERIOD_TAG = re.compile(r"-(q[1-4]|h[12]|fy)-(?:8k|10q|10k)(?:-|_|$)")

HALF_TAGS = ("H1", "H2")
FULL_YEAR_TAGS = ("FY", "H2")


def form_of(document: Document) -> str:
    """8-K, 10-Q, 10-K or OTHER, from the stem."""
    if document.doc_type != FILING:
        return OTHER
    stem = document.doc_id.lower()
    for pattern, form in _FORM_TOKENS:
        if re.search(pattern, stem):
            return form
    return OTHER


def fiscal_tag_of(document: Document) -> str | None:
    """The q1..q4 or fy tag in the stem, which is the filer's own label for the
    period the document reports on. More reliable than the index `period` field."""
    match = _PERIOD_TAG.search(document.doc_id.lower())
    return match.group(1).upper() if match else None


def call_kind_of(document: Document) -> str | None:
    """Which sort of call a transcript is.

    An earnings call and an investor-conference fireside chat are not the same
    evidence. The eight-call sequence has to be eight earnings calls or the
    "what did management stop saying" read compares a results call against a
    conference appearance and finds a change that is only a change of venue.
    """
    if document.doc_type != CALL_TRANSCRIPT:
        return None
    stem = document.doc_id.lower()
    if "-call-agm" in stem:
        return SHAREHOLDER_MEETING
    if "-call-conf" in stem:
        return CONFERENCE
    if "-call-" in stem:
        return EARNINGS_CALL
    return None


_SEGMENT = re.compile(r"-(qna|pres)(?:-\d+)?__")


def call_segment_of(document: Document) -> str | None:
    """Prepared remarks or Q&A. Both halves of one call, filed separately, and
    the trailing `-2`/`-3` on a re-filed section has to survive the match."""
    match = _SEGMENT.search(document.doc_id.lower())
    if match is None:
        return None
    return "qna" if match.group(1) == "qna" else "prepared"


def is_slide(document: Document) -> bool:
    return document.doc_type == SLIDE
