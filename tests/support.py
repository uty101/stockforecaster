"""Test helpers: a context with no file writes, and a throwaway corpus on disk."""

from __future__ import annotations

import tempfile
from datetime import date
from pathlib import Path

from forecaster.config import Config, Metric, Target
from forecaster.context import RunContext
from forecaster.events import NullSink
from forecaster.ledger import CostLedger

DEMO_TARGET = Target(
    company="Demo Corp",
    ticker="DEMO",
    period="FY2026Q3",
    output_file="DEMO-FY2026Q3.xlsx",
    metrics=(
        Metric(label="Revenue", units="USDm", kind="money"),
        Metric(label="Adjusted diluted EPS", units="USD / share", kind="per_share"),
        Metric(label="Adjusted gross margin", units="%", kind="percent"),
    ),
)


def make_context(config: Config, target: Target = DEMO_TARGET, as_of: date | None = None) -> RunContext:
    return RunContext(
        target=target,
        as_of=as_of or config.as_of,
        config=config,
        events=NullSink(),
        ledger=CostLedger(ceiling_usd=config.section("cost")["ceiling_usd"]),
        run_dir=Path(tempfile.mkdtemp(prefix="forecaster-test-")),
        run_id="test",
    )


def make_config(raw: dict, path: Path | None = None) -> Config:
    return Config(raw=raw, path=path or Path("<test>"))


def write_document(
    root: Path,
    company_dir: str,
    subdirectory: str,
    doc_id: str,
    published_at: str,
    document_type: str,
    body: str = "# Test document\n\nSome text.\n",
    ticker: str = "DEMO",
) -> Path:
    folder = root / company_dir / subdirectory
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{doc_id}.md"
    path.write_text(
        "---\n"
        f'company: "Demo Corp"\n'
        f'ticker: "{ticker}"\n'
        f'published_at: "{published_at}"\n'
        f'document_type: "{document_type}"\n'
        f'period: "Q3 2026"\n'
        "source_url: null\n"
        f'corpus_frozen_at: "2026-08-14"\n'
        "---\n\n" + body,
        encoding="utf-8",
    )
    return path


def demo_corpus_config(root: Path) -> dict:
    """A config pointing at a throwaway corpus directory rather than the real one.

    An absolute `root` wins over REPO_ROOT when the adapter joins them, which is
    how a test corpus is substituted without a second code path in the adapter.
    """
    return {
        "as_of": "2026-08-16",
        "corpus_frozen_at": "2026-08-14",
        "source_priority": ["corpus"],
        "sources": {
            "corpus": {
                "kind": "local_markdown_corpus",
                "root": str(root.resolve()),
                "company_directories": {"DEMO": "demo-corp"},
            }
        },
        "cost": {"ceiling_usd": 25.0, "cache_dir": ".forecaster-cache"},
    }
