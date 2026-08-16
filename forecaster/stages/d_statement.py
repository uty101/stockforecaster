"""The income statement, read out of the filings as printed.

The releases carry the statement as a table. This reads those tables rather than
asking a model to retype them, so every cell on the model sheet is the figure the
company printed, and the only thing between the filing and the screen is a
parser that can be tested.

Three shapes have to be handled. Home Depot prints one clean grid with the
periods in a header row. Analog Devices pads every value with empty cells and
puts the currency mark in a cell of its own, so a row arrives ragged and has to
be collapsed before it means anything. Hays prints a note column between the
label and the figures, and marks pence with a trailing p.

What this does not do is decide anything. It does not aggregate quarters into
halves, restate a prior period, or fill a gap. A line that a filing does not
carry is absent, and the sheet says so.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from ..documents import Document

# The heading above the statement, as each of these companies writes it.
HEADINGS = (
    "statements of earnings",
    "statement of earnings",
    "statements of income",
    "statement of income",
    "statements of consolidated income",
    "statement of consolidated income",
    "statements of operations",
    "statement of operations",
    "income statement",
)

# Headings that look close but are a different statement.
NOT_STATEMENTS = (
    "comprehensive income",
    "cash flow",
    "balance sheet",
    "financial position",
    "changes in equity",
    "stockholders' equity",
    "shareholders' equity",
)

PERIOD_WORDS = ("month", "months", "quarter", "year", "weeks", "period", "half")
MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}

# A subtotal is set apart from the lines above it on the sheet.
SUBTOTALS = (
    "gross profit", "gross margin", "total operating expenses", "operating income",
    "operating profit", "total costs and expenses", "income before income taxes",
    "earnings before provision for income taxes", "profit before tax", "net earnings",
    "net income", "net sales", "total revenue", "revenue", "turnover",
    "total net sales and revenues", "profit after tax", "total nonoperating expense",
)


@dataclass
class Column:
    """One period column, as the filing labels it."""

    label: str
    period_end: str | None = None

    @property
    def key(self) -> str:
        # Two filings write the same period differently, and the same end date
        # can belong to a three month and a six month column. Length and end
        # date together are what identify a period.
        return f"{span_of(self.label)}|{self.period_end or ''}"

    @property
    def display(self) -> str:
        span = span_of(self.label)
        if not self.period_end:
            return self.label
        year, month, day = self.period_end.split("-")
        names = {v: k.title() for k, v in MONTHS.items()}
        pretty = f"{int(day)} {names[int(month)]} {year}"
        return f"{span} to {pretty}" if span else pretty


@dataclass
class Statement:
    ticker: str
    columns: list[Column] = field(default_factory=list)
    rows: list[dict] = field(default_factory=list)
    sources: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def normalise_label(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip().lower()).strip(" :")


def parse_number(cell: str) -> float | None:
    """A printed figure, or None where the filing prints nothing.

    A dash is the company writing zero or not applicable. Either way it is not a
    number, and inventing one here would put a figure on the screen that no
    filing carries.
    """
    text = str(cell or "").strip()
    if not text or text in {"—", "-", "–", "N/A", "n/a", "*"}:
        return None
    negative = text.startswith("(") and ")" in text
    cleaned = re.sub(r"[()$£€,%]", "", text).strip()
    cleaned = re.sub(r"(?<=\d)p$", "", cleaned).strip()
    if not re.fullmatch(r"-?\d+(\.\d+)?", cleaned):
        return None
    value = float(cleaned)
    return -value if negative and value > 0 else value


def parse_date(text: str) -> str | None:
    """The period end a column header names, where it names one."""
    match = re.search(r"([A-Za-z]+)\s+(\d{1,2}),?\s+(\d{4})", str(text))
    if match and match.group(1).lower() in MONTHS:
        month = MONTHS[match.group(1).lower()]
        return f"{int(match.group(3)):04d}-{month:02d}-{int(match.group(2)):02d}"
    match = re.search(r"(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})", str(text))
    if match and match.group(2).lower() in MONTHS:
        month = MONTHS[match.group(2).lower()]
        return f"{int(match.group(3)):04d}-{month:02d}-{int(match.group(1)):02d}"
    return None


def split_row(line: str) -> list[str]:
    """A markdown row into cells, with the padding taken out.

    Analog Devices pads each figure with empty cells and puts the currency mark
    in its own cell. Dropping both is what makes a ragged row line up with the
    header above it.
    """
    if not line.strip().startswith("|"):
        return []
    # Deere pads with a zero width space rather than with nothing, which reads as
    # a cell with content in it unless it is taken out here.
    scrubbed = line.replace("​", "").replace("﻿", "")
    cells = [cell.strip() for cell in scrubbed.strip().strip("|").split("|")]
    return [cell for cell in cells if cell not in {"", "$", "£", "€"}]


def is_divider(line: str) -> bool:
    return bool(re.fullmatch(r"\|[\s|:-]+\|", line.strip()))


def find_tables(text: str) -> list[tuple[str, list[str], str]]:
    """Every statement table in a document, with the heading and caption above it.

    Deere names its periods in the line above the table and leaves the header row
    holding nothing but years, so the caption travels with the block.
    """
    lines = text.splitlines()
    tables: list[tuple[str, list[str], str]] = []
    heading = ""
    caption = ""

    index = 0
    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        if stripped and not stripped.startswith("|"):
            lowered = stripped.lower()
            if any(word in lowered for word in HEADINGS) and len(stripped) < 200:
                heading = stripped.lstrip("#").strip()
                caption = ""
            elif stripped.startswith("#"):
                heading = stripped.lstrip("#").strip()
                caption = ""
            elif heading and parse_date(stripped) and len(stripped) < 200:
                caption = stripped
        if stripped.startswith("|"):
            block = []
            while index < len(lines) and lines[index].strip().startswith("|"):
                block.append(lines[index])
                index += 1
            if heading and len(block) > 4:
                tables.append((heading, block, caption))
            # A heading covers the table under it and nothing further. Without
            # this, every later table in the document inherits the statement
            # heading and arrives as extra lines that are not in the statement.
            heading = ""
            caption = ""
            continue
        index += 1

    keep = []
    for heading, block, caption in tables:
        lowered = heading.lower()
        if any(word in lowered for word in NOT_STATEMENTS):
            continue
        if not any(word in lowered for word in HEADINGS):
            continue
        # A periodic report carries notes that reference the statement in their
        # own heading, so the heading alone is not enough. A statement runs from
        # a top line down to earnings per share, and a table missing either end
        # is a note rather than the statement.
        body = "\n".join(block).lower()
        has_top = re.search(r"(net sales|revenue|turnover|net fees)", body)
        has_bottom = re.search(
            r"(net income|net earnings|net loss|profit.{0,20}after tax|profit for the (period|year)|per share)",
            body,
        )
        if has_top and has_bottom:
            keep.append((heading, block, caption))
    return keep


SPAN_WORDS = (
    ("three months", "Three months"),
    ("six months", "Six months"),
    ("nine months", "Nine months"),
    ("twelve months", "Twelve months"),
    ("first quarter", "Three months"),
    ("second quarter", "Three months"),
    ("third quarter", "Three months"),
    ("fourth quarter", "Three months"),
    ("half", "Six months"),
    ("year", "Year"),
)


def span_of(label: str) -> str:
    """How long a period a column covers, in the words the filings use.

    Two columns can end on the same day and cover different lengths, so the span
    is half of a column's identity. Without it a six month figure would land in
    the three month column.
    """
    lowered = str(label or "").lower()
    for needle, name in SPAN_WORDS:
        if needle in lowered:
            return name
    return ""


def caption_dates(caption: str) -> list[str]:
    """Every period end named in the line above a table."""
    found = []
    for match in re.finditer(r"([A-Za-z]+\s+\d{1,2},?\s+\d{4})|(\d{1,2}\s+[A-Za-z]+\s+\d{4})", str(caption or "")):
        parsed = parse_date(match.group(0))
        if parsed and parsed not in found:
            found.append(parsed)
    return found


def read_columns(block: list[str], caption: str = "") -> tuple[list[Column], int]:
    """The period columns, and the row the figures start on.

    A header can arrive as one row of dates, or as a row of spans above a row of
    dates. Where the spans divide the dates evenly each span is repeated across
    its own dates, which is how a filing showing three months beside six months
    reads.
    """
    spans: list[str] = []
    dates: list[str] = []
    years: list[str] = []
    first_value_row = 0

    for index, line in enumerate(block):
        if is_divider(line):
            continue
        cells = split_row(line)
        if not cells:
            continue
        # Most filings put a label in the first cell of the header row. Analog
        # Devices leaves it empty, so after the padding is dropped the row starts
        # with a period rather than a label, and taking cells[1:] would lose the
        # first period.
        leads_with_period = bool(cells) and (
            parse_date(cells[0]) is not None
            or any(word in cells[0].lower() for word in PERIOD_WORDS)
            or re.fullmatch(r"(19|20)\d{2}", cells[0]) is not None
        )
        body = cells if leads_with_period else cells[1:] if len(cells) > 1 else []
        has_date = any(parse_date(cell) for cell in body)
        has_period_word = any(
            any(word in cell.lower() for word in PERIOD_WORDS) for cell in body
        )
        has_number = any(parse_number(cell) is not None for cell in body)

        if has_date and not has_number:
            dates = body
            first_value_row = index + 1
            continue
        if has_period_word and not has_number and not has_date:
            spans = body
            first_value_row = index + 1
            continue
        if has_date and has_period_word:
            dates = body
            first_value_row = index + 1
            continue
        # Deere heads its columns with the year alone and names the period ends
        # in the caption above the table.
        if not dates and body and all(re.fullmatch(r"(19|20)\d{2}", cell) for cell in body):
            years = body
            first_value_row = index + 1
            continue

        if has_number:
            if not dates and not spans and not years:
                continue
            first_value_row = index
            break

    if years and not dates:
        available = caption_dates(caption)
        resolved = [next((d for d in available if d.startswith(year)), None) for year in years]
        if any(resolved):
            ends = resolved
            labels = [
                f"{spans[i * len(spans) // len(years)]} {years[i]}".strip()
                if spans and len(years) >= len(spans)
                else years[i]
                for i in range(len(years))
            ]
            columns = [
                Column(label=re.sub(r"\s+", " ", label).strip(), period_end=end)
                for label, end in zip(labels, ends)
            ]
            return columns, first_value_row

    labels = dates or spans
    if dates and spans and len(spans) > 1 and len(dates) % len(spans) == 0:
        # A filing showing three months beside six months repeats each span
        # across its own dates.
        repeat = len(dates) // len(spans)
        labels = [f"{spans[i // repeat]} {dates[i]}".strip() for i in range(len(dates))]
    elif dates and spans and len(spans) == 1:
        labels = [f"{spans[0]} {value}".strip() for value in dates]
    elif dates and spans:
        # The spans do not divide the dates, which happens when a trailing
        # column holds the change rather than a period. Pair by position and let
        # the unpaired column fall away for having no date.
        labels = [
            f"{spans[i]} {dates[i]}".strip() if i < len(spans) else dates[i]
            for i in range(len(dates))
        ]

    columns = [Column(label=re.sub(r"\s+", " ", label).strip(), period_end=parse_date(label)) for label in labels]
    return columns, first_value_row


def read_table(heading: str, block: list[str], caption: str = "") -> tuple[list[Column], list[dict]]:
    """The lines of one table, aligned to the periods above them.

    Alignment is by count rather than by cell position, because the three
    formats disagree about position. A row carries its figures in order, so a row
    holding as many figures as the header holds columns lines up with the header,
    and a row holding as many figures as there are dated columns lines up with
    those. Anything else is left out rather than guessed at.
    """
    columns, start = read_columns(block, caption)
    if not columns:
        return [], []

    dated = [column for column in columns if column.period_end]
    if not dated:
        return [], []

    rows: list[dict] = []
    skipped = 0
    for line in block[start:]:
        if is_divider(line):
            continue
        cells = split_row(line)
        if not cells:
            continue
        label = cells[0].strip()
        if not label or parse_number(label) is not None:
            continue

        numbers = [parse_number(cell) for cell in cells[1:]]
        numbers = [value for value in numbers if value is not None]

        if not numbers:
            # A heading inside the statement, such as the line above the
            # operating expense breakdown. It carries no figure and is kept so
            # the statement reads in the order the company prints it.
            rows.append(
                {
                    "label": re.sub(r"\s+", " ", label),
                    "values": [None] * len(dated),
                    "quote": re.sub(r"\s+", " ", line.strip()),
                    "heading": heading,
                    "subtotal": normalise_label(label) in SUBTOTALS,
                    "header_only": True,
                }
            )
            continue

        if len(numbers) == len(columns):
            values = [
                value for column, value in zip(columns, numbers) if column.period_end
            ]
        elif len(numbers) == len(dated):
            values = numbers
        else:
            skipped += 1
            continue

        rows.append(
            {
                "label": re.sub(r"\s+", " ", label),
                "values": values,
                "quote": re.sub(r"\s+", " ", line.strip()),
                "heading": heading,
                "subtotal": normalise_label(label) in SUBTOTALS,
                "header_only": False,
            }
        )
    return dated, rows


def build(ticker: str, documents: list[Document], limit: int = 90) -> Statement:
    """Merge the statement across filings, newest first.

    The newest release sets the order of the lines, because that is the order the
    company presents them in now. Older releases fill in the periods to its left
    and never reorder it.
    """
    statement = Statement(ticker=ticker)
    ordered = sorted(documents, key=lambda d: d.published_at, reverse=True)[:limit]
    by_key: dict[str, dict] = {}
    columns: dict[str, Column] = {}

    for document in ordered:
        try:
            text = document.text()
        except OSError:
            continue
        for heading, block, caption in find_tables(text):
            table_columns, rows = read_table(heading, block, caption)
            if not table_columns or not rows:
                continue
            statement.sources.append(
                {
                    "doc_id": document.doc_id,
                    "published_at": document.published_at.isoformat(),
                    "title": document.title,
                    "heading": heading,
                    "columns": [c.label for c in table_columns],
                }
            )
            for column in table_columns:
                columns.setdefault(column.key, column)
            for row in rows:
                key = normalise_label(row["label"])
                if not key:
                    continue
                entry = by_key.setdefault(
                    key,
                    {
                        "label": row["label"],
                        "subtotal": row["subtotal"],
                        "header_only": row["header_only"],
                        "cells": {},
                    },
                )
                for column, value in zip(table_columns, row["values"]):
                    if value is None:
                        continue
                    # The first filing to carry a period is the one that reported
                    # it. A later release restating it does not overwrite it.
                    entry["cells"].setdefault(
                        column.key,
                        {
                            "value": value,
                            "doc_id": document.doc_id,
                            "quote": row["quote"][:400],
                            "published_at": document.published_at.isoformat(),
                        },
                    )

    dated = [c for c in columns.values() if c.period_end]
    dated.sort(key=lambda c: (c.period_end or "", c.label))
    statement.columns = dated

    for entry in by_key.values():
        cells = [entry["cells"].get(column.key) for column in statement.columns]
        if entry["header_only"] and not any(cells):
            continue
        if not any(cells):
            continue
        statement.rows.append(
            {
                "label": entry["label"],
                "subtotal": entry["subtotal"],
                "cells": cells,
            }
        )

    if not statement.rows:
        statement.notes.append("No filing in the corpus prints the statement as a table.")
    return statement


def to_dict(statement: Statement) -> dict:
    return {
        "ticker": statement.ticker,
        "columns": [
            {"label": c.display, "as_reported": c.label, "span": span_of(c.label), "period_end": c.period_end}
            for c in statement.columns
        ],
        "rows": statement.rows,
        "sources": statement.sources,
        "notes": statement.notes,
    }


def run(ticker: str, documents: list[Document]) -> dict:
    return to_dict(build(ticker, documents))


def _main() -> None:
    """Write a statement into each newest run directory.

    Kept separate from the run so the statement can be rebuilt from the filings
    without paying for the reasoning again.
    """
    from ..config import REPO_ROOT, load_config
    from ..sources.corpus import CorpusSource

    config = load_config()
    corpus = CorpusSource(config.source("corpus"))
    runs = REPO_ROOT / "runs"

    definitions = json.loads((REPO_ROOT / "challenge" / "companies.json").read_text(encoding="utf-8"))
    for company in definitions["companies"]:
        ticker = company["ticker"]
        prefix = f"{ticker.replace(':', '_')}-"
        candidates = [
            path
            for path in runs.iterdir()
            if path.is_dir() and path.name.startswith(prefix) and (path / "results.json").exists()
        ]
        if not candidates:
            print(f"statement: no run for {ticker}")
            continue
        newest = max(candidates, key=lambda p: (p / "results.json").stat().st_mtime)
        as_of = date.fromisoformat(
            json.loads((newest / "results.json").read_text(encoding="utf-8"))["header"]["as_of"]
        )
        documents = corpus.filings(ticker, as_of) or []
        statement = run(ticker, documents)
        (newest / "statement.json").write_text(json.dumps(statement, indent=1), encoding="utf-8")
        print(
            f"statement: {ticker} {len(statement['rows'])} lines, "
            f"{len(statement['columns'])} periods, {len(statement['sources'])} filings"
        )


if __name__ == "__main__":
    _main()
