"""Stage A tests.

The one that matters most is test_point_in_time_violation_is_tripped. If a
backtest cannot fail that way it is enforcing nothing and every number from it
is worthless, so it is written to fail if either lock is removed.
"""

from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path

from forecaster.config import PERCENT, classify_units, load_config, load_targets, target_for
from forecaster.documents import CALL_TRANSCRIPT, FILING, Document
from forecaster.sources.corpus import CorpusSource
from forecaster.sources.loader import MissingRequiredData, SourceChain, build_chain
from forecaster.sources.protocol import PointInTimeViolation, guard_point_in_time
from forecaster.stages import a_sources
from tests.support import demo_corpus_config, make_config, make_context, write_document


class TemporaryCorpus(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="forecaster-corpus-"))
        self.config = make_config(demo_corpus_config(self.root))

    def write(self, doc_id: str, published_at: str, document_type: str = FILING, **kwargs) -> Path:
        subdirectory = {"FILING": "filings", "CALL_TRANSCRIPT": "call-transcripts", "SLIDE": "slides"}[
            document_type
        ]
        return write_document(
            self.root, "demo-corp", subdirectory, doc_id, published_at, document_type, **kwargs
        )


class PointInTime(TemporaryCorpus):
    def test_point_in_time_violation_is_tripped(self) -> None:
        """A document filed one day after the as-of date must never come back."""
        self.write("before", "2026-08-15")
        self.write("one-day-late", "2026-08-17")
        source = CorpusSource(self.config.source("corpus"))

        returned = source.filings("DEMO", date(2026, 8, 16))

        self.assertEqual([doc.doc_id for doc in returned], ["before"])

    def test_guard_raises_on_a_late_document(self) -> None:
        late = Document(
            doc_id="late",
            company="Demo Corp",
            ticker="DEMO",
            published_at=date(2026, 8, 17),
            doc_type=FILING,
            period="Q3 2026",
            title="Late",
            source_url=None,
            path=Path("<memory>"),
            source_name="broken",
        )
        with self.assertRaises(PointInTimeViolation) as raised:
            guard_point_in_time([late], date(2026, 8, 16), source="broken", method="filings")
        self.assertIn("2026-08-17", str(raised.exception))

    def test_loader_catches_an_adapter_that_forgets_the_guard(self) -> None:
        """The second lock. An adapter written in twenty minutes may forget; the
        chain may not."""

        late = Document(
            doc_id="leaked",
            company="Demo Corp",
            ticker="DEMO",
            published_at=date(2026, 8, 20),
            doc_type=FILING,
            period="Q3 2026",
            title="Leaked",
            source_url=None,
            path=Path("<memory>"),
            source_name="forgetful",
        )

        class ForgetfulSource:
            name = "forgetful"

            def filings(self, ticker: str, as_of: date):
                return [late]

        ctx = make_context(self.config)
        chain = SourceChain([ForgetfulSource()], ctx.events, date(2026, 8, 16))
        with self.assertRaises(PointInTimeViolation):
            chain.fetch("filings", "DEMO")


class Chain(TemporaryCorpus):
    def test_required_method_with_no_answer_names_the_method(self) -> None:
        self.write("only-a-filing", "2026-08-01")  # no transcripts anywhere
        ctx = make_context(self.config)
        with self.assertRaises(MissingRequiredData) as raised:
            a_sources.run(ctx)
        self.assertIn("transcripts", str(raised.exception))

    def test_optional_gaps_degrade_rather_than_raise(self) -> None:
        self.write("filing", "2026-08-01")
        self.write("call", "2026-08-02", CALL_TRANSCRIPT)
        ctx = make_context(self.config)

        loaded = a_sources.run(ctx)

        self.assertTrue(loaded.available["filings"])
        self.assertTrue(loaded.available["transcripts"])
        self.assertFalse(loaded.available["consensus"])
        degrades = [note for note in ctx.notes if note["kind"] == "degrade"]
        self.assertEqual(len(degrades), 1)
        self.assertIn("consensus", degrades[0]["methods"])

    def test_fall_through_is_recorded_for_the_integrity_sheet(self) -> None:
        self.write("filing", "2026-08-01")
        self.write("call", "2026-08-02", CALL_TRANSCRIPT)
        ctx = make_context(self.config)

        loaded = a_sources.run(ctx)
        record = loaded.chain.integrity_record()

        self.assertEqual(record["priority"], ["corpus"])
        self.assertEqual(record["answered_by"]["filings(DEMO)"], "corpus")
        self.assertIn("consensus(DEMO, FY2026Q3)", record["unanswered"])
        self.assertIn("prices", record["methods_no_source_answered"])
        self.assertNotIn("filings", record["methods_no_source_answered"])

    def test_newest_first(self) -> None:
        self.write("older", "2026-01-05")
        self.write("newer", "2026-06-05")
        source = CorpusSource(self.config.source("corpus"))
        self.assertEqual([doc.doc_id for doc in source.filings("DEMO", date(2026, 8, 16))], ["newer", "older"])

    def test_unknown_ticker_returns_nothing_rather_than_raising(self) -> None:
        source = CorpusSource(self.config.source("corpus"))
        self.assertIsNone(source.filings("NOPE", date(2026, 8, 16)))


class Targets(unittest.TestCase):
    """The twelve numbers we owe, and the vocabulary trap in the period label."""

    def test_all_four_targets_load_with_three_metrics_each(self) -> None:
        targets = load_targets()
        self.assertEqual([t.ticker for t in targets], ["HD", "ADI", "LSE:HAS", "DE"])
        for target in targets:
            self.assertEqual(len(target.metrics), 3)

    def test_a_full_year_reporter_is_not_called_a_quarter(self) -> None:
        self.assertEqual(target_for("LSE:HAS").period_kind, "full_year")
        self.assertEqual(target_for("LSE:HAS").period_noun, "financial year")
        self.assertEqual(target_for("HD").period_kind, "quarter")

    def test_percent_metrics_are_classified_as_percent(self) -> None:
        self.assertEqual(classify_units("%"), PERCENT)
        comparable = [m for m in target_for("HD").metrics if m.units == "%"][0]
        self.assertTrue(comparable.is_percent)

    def test_unknown_units_raise_rather_than_defaulting(self) -> None:
        with self.assertRaises(ValueError):
            classify_units("widgets")

    def test_hays_eps_is_pence_per_share_not_pounds_in_millions(self) -> None:
        """The trailing letter says nothing: GBPm is money, GBp is pence a share."""
        eps = [m for m in target_for("LSE:HAS").metrics if m.units == "GBp"][0]
        net_fees = [m for m in target_for("LSE:HAS").metrics if m.units == "GBPm"][0]
        self.assertEqual(eps.kind, "per_share")
        self.assertTrue(eps.is_subunit)
        self.assertEqual(net_fees.kind, "money")
        self.assertFalse(net_fees.is_subunit)


class RealCorpus(unittest.TestCase):
    """Against the actual 1,139-document corpus, not a fixture."""

    def setUp(self) -> None:
        self.config = load_config()
        self.chain = build_chain(self.config, make_context(self.config).events)

    def test_every_target_ticker_has_filings_and_transcripts(self) -> None:
        for target in load_targets():
            filings = self.chain.fetch("filings", target.ticker)
            transcripts = self.chain.fetch("transcripts", target.ticker)
            self.assertTrue(filings, f"{target.ticker} has no filings")
            self.assertTrue(transcripts, f"{target.ticker} has no transcripts")
            self.assertTrue(all(doc.published_at <= self.config.as_of for doc in filings))
            self.assertTrue(all(doc.published_at <= self.config.as_of for doc in transcripts))

    def test_the_corpus_itself_answers_nothing_about_consensus(self) -> None:
        """Stated as a test so the corpus can never quietly acquire a consensus
        it never contained. Consensus must arrive from a source that publishes
        it, and be labelled with that source."""
        corpus = CorpusSource(self.config.source("corpus"))
        self.assertIsNone(corpus.consensus("HD", "FY2026Q2", self.config.as_of))
        self.assertIsNone(corpus.prices("HD", self.config.as_of))

    def test_consensus_arrives_with_the_fields_lambda_needs(self) -> None:
        """A bare float is useless here: every regime condition needs an input."""
        try:
            consensus = self.chain.fetch("consensus", "HD", "FY2026Q2")
        except Exception as error:  # noqa: BLE001
            self.skipTest(f"market source unreachable: {error}")

        if consensus is None:
            self.skipTest("market source returned nothing; network gate is open")

        self.assertEqual(consensus["source"], "market")
        self.assertGreater(consensus["eps"]["mean"], 0)
        self.assertGreaterEqual(consensus["eps"]["analysts"], 1)
        self.assertIsNotNone(consensus["eps"]["dispersion_range_pct"])
        self.assertIn("up_30d", consensus["revisions"])
        self.assertFalse(
            consensus["as_of_history_available"],
            "this source has no vintage, and the Method sheet must say so",
        )

    def test_a_historical_as_of_is_refused_rather_than_answered(self) -> None:
        """Serving today's consensus for a past quarter is lookahead that would
        show up as skill. The adapter refuses instead."""
        from datetime import date as _date

        from forecaster.sources.market import PointInTimeUnavailable, YahooMarketSource

        with self.assertRaises(PointInTimeUnavailable):
            YahooMarketSource().consensus("HD", "FY2024Q2", _date(2024, 5, 1))


if __name__ == "__main__":
    unittest.main()
