"""Stage B tests.

The one that matters most is the Hays case: on 2026-07-10 Hays filed its fourth
quarter trading statement, and the filings immediately around it are voting
rights and own-share notifications. Ranking by date finds the wrong document.
Ranking by form finds the right one.
"""

from __future__ import annotations

import json
import unittest
from datetime import date

from forecaster.config import load_config, target_for
from forecaster.forms import EIGHT_K, TEN_K, TEN_Q, call_kind_of, form_of
from forecaster.stages import a_sources, b_acquire
from tests.support import make_context


class RealCorpusAcquire(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_config()

    def dossier_for(self, ticker: str) -> b_acquire.Dossier:
        ctx = make_context(self.config, target_for(ticker))
        return b_acquire.run(ctx, a_sources.run(ctx))

    def test_the_form_filter_finds_the_trading_statement_in_a_noisy_filer(self) -> None:
        """Hays. Four of the five newest filings by date are notifications."""
        ctx = make_context(self.config, target_for("LSE:HAS"))
        sources = a_sources.run(ctx)
        filings = sources.chain.fetch("filings", "LSE:HAS")

        newest_three_by_date = filings[:3]
        self.assertTrue(
            any(form_of(doc) == "OTHER" for doc in newest_three_by_date),
            "expected the naive date ranking to be polluted; if it is not, this test proves nothing",
        )

        dossier = b_acquire.run(ctx, sources)
        latest = dossier.latest_earnings_release

        self.assertIsNotNone(latest)
        self.assertEqual(form_of(latest), EIGHT_K)
        self.assertEqual(latest.published_at, date(2026, 7, 10))
        self.assertIn("Trading Update", latest.title)

    def test_every_target_gets_releases_reports_and_a_call_sequence(self) -> None:
        for ticker in ("HD", "ADI", "LSE:HAS", "DE"):
            with self.subTest(ticker=ticker):
                dossier = self.dossier_for(ticker)
                self.assertTrue(dossier.earnings_releases, "no 8-K earnings releases")
                self.assertTrue(dossier.periodic_reports, "no 10-Q or 10-K reports")
                self.assertEqual(len(dossier.call_sequence), 8)

    def test_a_uk_filer_with_no_10q_substitutes_its_results_announcements_out_loud(self) -> None:
        """Hays files halves, not quarters, and no 10-Q or 10-K exists for it."""
        ctx = make_context(self.config, target_for("LSE:HAS"))
        dossier = b_acquire.run(ctx, a_sources.run(ctx))

        self.assertTrue(dossier.periodic_reports)
        self.assertTrue(all(form_of(doc) == EIGHT_K for doc in dossier.periodic_reports))
        substitution = [
            note for note in ctx.notes if "files no 10-Q or 10-K" in note["message"]
        ]
        self.assertTrue(substitution, "the substitution must announce itself")

    def test_us_filers_take_statements_from_10q_and_10k(self) -> None:
        for ticker in ("HD", "ADI", "DE"):
            with self.subTest(ticker=ticker):
                dossier = self.dossier_for(ticker)
                self.assertTrue(
                    all(form_of(doc) in (TEN_Q, TEN_K) for doc in dossier.periodic_reports)
                )

    def test_the_call_sequence_is_ordered_oldest_first_and_holds_earnings_calls_only(self) -> None:
        dossier = self.dossier_for("HD")
        dates = [call.held_on for call in dossier.call_sequence]

        self.assertEqual(dates, sorted(dates), "the sequence must read in the order it happened")
        for call in dossier.call_sequence:
            for section in call.sections:
                self.assertEqual(call_kind_of(section.document), "EARNINGS_CALL")

    def test_nothing_in_the_dossier_postdates_the_as_of(self) -> None:
        dossier = self.dossier_for("DE")
        for document in dossier.all_documents:
            self.assertLessEqual(document.published_at, self.config.as_of, document.doc_id)

    def test_what_is_absent_is_named_rather_than_omitted(self) -> None:
        dossier = self.dossier_for("ADI")
        self.assertIn("consensus", dossier.absent)
        self.assertIn("peers", dossier.absent)
        self.assertTrue(all(reason for reason in dossier.absent.values()))

    def test_budget_exhaustion_is_recorded_at_the_moment_of_loss(self) -> None:
        raw = dict(load_config().raw)
        raw["acquire_budgets"] = dict(raw["acquire_budgets"], transcript_sequence_length=2)
        from tests.support import make_config

        ctx = make_context(make_config(raw), target_for("HD"))
        dossier = b_acquire.run(ctx, a_sources.run(ctx))

        self.assertEqual(len(dossier.call_sequence), 2)
        skips = [entry for entry in dossier.skipped if entry["acquirer"] == "earnings_calls"]
        self.assertTrue(skips, "a truncated sequence must say what it dropped")
        drop_events = [note for note in ctx.notes if note["kind"] == "drop"]
        self.assertTrue(drop_events)

    def test_the_dossier_is_written_and_reloadable(self) -> None:
        ctx = make_context(self.config, target_for("HD"))
        dossier = b_acquire.run(ctx, a_sources.run(ctx))
        written = list(ctx.run_dir.glob("dossier-*.json"))

        self.assertEqual(len(written), 1)
        payload = json.loads(written[0].read_text(encoding="utf-8"))
        self.assertEqual(payload["ticker"], "HD")
        self.assertEqual(payload["counts"]["earnings_calls"], 8)
        self.assertEqual(len(payload["call_sequence"]), 8)
        self.assertTrue(payload["paths"])
        self.assertEqual(len(dossier.all_documents), len(payload["paths"]))


class FormClassification(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_config()
        ctx = make_context(self.config, target_for("ADI"))
        self.filings = a_sources.run(ctx).chain.fetch("filings", "ADI")

    def test_the_index_period_field_is_not_trusted(self) -> None:
        """The ADI 10-Q published 2026-05-20 is labelled Q3 2026 in the index and
        is the Q2 report. The stem says q2; nothing reads the label."""
        report = [
            doc
            for doc in self.filings
            if doc.published_at == date(2026, 5, 20) and form_of(doc) == TEN_Q
        ][0]
        self.assertEqual(report.period, "Q3 2026")
        from forecaster.forms import fiscal_tag_of

        self.assertEqual(fiscal_tag_of(report), "Q2")

    def test_annual_reports_classify_as_10k_not_10q(self) -> None:
        annual = [doc for doc in self.filings if form_of(doc) == TEN_K]
        self.assertTrue(annual)
        self.assertTrue(all("10k" in doc.doc_id for doc in annual))


if __name__ == "__main__":
    unittest.main()
