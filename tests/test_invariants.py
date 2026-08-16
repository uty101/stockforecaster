"""The tests that matter most.

These are the ones from the brief that check an invariant cannot be broken,
rather than that a function returns the right thing. Several assert on the shape
of the code itself, because the property being protected is structural: a lens
cannot see another lens's view if there is no parameter through which one could
arrive, and that is checkable.
"""

from __future__ import annotations

import inspect
import json
import multiprocessing
import time
import unittest
from pathlib import Path

from forecaster import nodes
from forecaster.config import REPO_ROOT, load_config, target_for
from forecaster.stages import (
    b5_extract,
    c_structure,
    d_model,
    e_analyse,
    g_judge,
    h_position,
    i_output,
    v1_reconcile,
)
from tests.support import make_context


class LensIsolation(unittest.TestCase):
    def test_no_lens_signature_can_accept_another_lens_view(self) -> None:
        """Structural, not a promise in a prompt.

        The only function that builds a lens call is e_analyse.run. If neither it
        nor the corpus builder can be handed a LensView or LensViews, there is no
        path by which one lens's output could reach another.
        """
        for function in (e_analyse.run, e_analyse.build_corpus):
            for name, parameter in inspect.signature(function).parameters.items():
                annotation = str(parameter.annotation)
                self.assertNotIn(
                    "LensView",
                    annotation,
                    f"{function.__name__} takes {name}: {annotation} — a lens could see another lens",
                )

    def test_the_corpus_a_lens_reads_contains_no_lens_output(self) -> None:
        source = inspect.getsource(e_analyse.build_corpus)
        for forbidden in ("views", "LensView", "surviving", "estimates"):
            self.assertNotIn(
                f"{forbidden}.",
                source,
                f"the shared corpus references {forbidden}; lenses that see each other converge",
            )


class ConsensusBusIsolation(unittest.TestCase):
    """The bus is tapped once and terminates at positioning."""

    def test_only_the_positioning_stage_accepts_the_bus(self) -> None:
        reasoning_stages = (
            b5_extract.run,
            c_structure.run,
            d_model.run,
            e_analyse.run,
            v1_reconcile.run,
            g_judge.run,
        )
        for function in reasoning_stages:
            for name, parameter in inspect.signature(function).parameters.items():
                self.assertNotIn(
                    "ConsensusBus",
                    str(parameter.annotation),
                    f"{function.__module__}.{function.__name__} takes the consensus bus as {name}",
                )

        self.assertIn(
            "ConsensusBus",
            str(inspect.signature(h_position.run).parameters["bus"].annotation),
            "positioning is the one stage that may read the bus",
        )

    def test_the_bus_exposes_nothing_a_reasoning_stage_could_write_to(self) -> None:
        writable = [
            name
            for name in dir(h_position.ConsensusBus)
            if name.startswith("set_") or name.startswith("update")
        ]
        self.assertEqual(writable, [], "the bus must be carried untouched")


class BaselineTerminates(unittest.TestCase):
    def test_the_baseline_cannot_feed_the_forecast(self) -> None:
        """It renders beside the forecast and is never an input to it.

        The forecast for every metric must equal either the own estimate or the
        consensus-anchored blend — never anything computed from the baseline.
        """
        source = inspect.getsource(h_position.run)
        self.assertNotIn("baseline +", source)
        self.assertNotIn("+ baseline", source)
        self.assertNotIn("baseline *", source)
        for line in source.splitlines():
            stripped = line.strip()
            if stripped.startswith("forecast") and "=" in stripped:
                self.assertNotIn(
                    "baseline", stripped, f"the baseline reached the forecast: {stripped}"
                )


class DeepTierBudget(unittest.TestCase):
    def test_only_the_judge_runs_on_the_deep_tier(self) -> None:
        deep = [node for node in nodes.ALL if node.tier == nodes.DEEP]
        self.assertEqual([node.agent for node in deep], ["judge"])


class TotalWipeout(unittest.TestCase):
    def test_every_lens_dropped_raises_rather_than_forecasting(self) -> None:
        from forecaster.stages.e_analyse import LensView, LensViews

        config = load_config()
        ctx = make_context(config, target_for("HD"))
        store = c_structure.EvidenceStore(ticker="HD")
        store.add(
            c_structure.Claim(
                citation_id="HD-001",
                kind=c_structure.PROSE,
                label="Net sales",
                value=1.0,
                units="billions",
                basis="as-reported",
                period_label="Q1",
                period_end=None,
                quote="net sales were one billion",
                doc_id="doc",
                source="corpus",
            )
        )
        views = LensViews(
            views=[
                LensView(
                    node_id="U13",
                    lens="lens_guidance",
                    payload={
                        "estimates": [
                            {
                                "metric_label": "Net sales",
                                "estimate": 1.0,
                                "confidence": 0.5,
                                "reasoning": "",
                                "citations": [{"citation_id": "INVENTED-999", "quote": "x"}],
                            }
                        ],
                        "what_would_change_my_mind": "",
                    },
                )
            ]
        )
        with self.assertRaises(v1_reconcile.EveryLensDropped):
            v1_reconcile.run(ctx, views, store)


class AtomicResults(unittest.TestCase):
    def test_the_results_file_is_never_observed_half_written(self) -> None:
        """Poll a reader while a large document is written repeatedly.

        Without the temporary-file-and-rename the reader eventually parses a
        truncated document, which surfaces as a confusing complaint about an
        unexpected token rather than anything pointing at the cause.
        """
        target = Path(REPO_ROOT) / "runs" / "_atomic_probe" / "results.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = {"filler": ["x" * 400] * 500, "n": 0}

        reads, failures = 0, 0
        for n in range(12):
            payload["n"] = n
            i_output.write_atomic(target, payload)
            for _ in range(4):
                try:
                    loaded = json.loads(target.read_text(encoding="utf-8"))
                    self.assertIn("n", loaded)
                    reads += 1
                except json.JSONDecodeError:
                    failures += 1

        self.assertGreater(reads, 0)
        self.assertEqual(failures, 0, "a reader saw a partially written results file")


class RosterMatchesPrompts(unittest.TestCase):
    def test_every_prompt_file_has_a_node_and_every_agent_node_has_a_prompt(self) -> None:
        """A new prompt file without a roster entry must fail, because a
        hand-kept roster drifts and then confidently describes a system that is
        no longer running."""
        directory = REPO_ROOT / "llm" / "prompts"
        on_disk = {path.stem for path in directory.glob("*.md")}
        in_registry = {node.agent for node in nodes.agents() if node.agent}

        self.assertEqual(
            on_disk - in_registry, set(), "prompt file with no node in the registry"
        )
        self.assertEqual(
            in_registry - on_disk, set(), "node names a prompt that does not exist"
        )

    def test_every_prompt_declares_a_version(self) -> None:
        from forecaster.agents.prompts import PromptStore

        store = PromptStore()
        for path in (REPO_ROOT / "llm" / "prompts").glob("*.md"):
            prompt = store.get(path.stem)
            self.assertTrue(prompt.version, f"{path.stem} has no version")
            self.assertIn(prompt.tier, ("none", "cheap", "mid", "deep"))


class PeriodVocabulary(unittest.TestCase):
    def test_a_half_year_label_reaches_the_lens_prompts(self) -> None:
        """Hand a full-year reporter a quarter-shaped prompt and every lens
        abstains honestly, nothing raises, and the run dies downstream looking
        like a model problem when it is a vocabulary problem."""
        from forecaster.agents.prompts import PromptStore

        target = target_for("LSE:HAS")
        self.assertEqual(target.period_kind, "full_year")

        rendered = PromptStore().get("lens_guidance").render(
            company=target.company,
            ticker=target.ticker,
            period=target.period,
            period_noun=target.period_noun,
            metric_focus="net fees",
        )
        self.assertIn("financial year", rendered)
        self.assertNotIn("a quarter,", rendered)


if __name__ == "__main__":
    unittest.main()


class NarrativeCannotInventAFigure(unittest.TestCase):
    """Prose that invents a number is worse than prose that omits one, because
    it reads exactly as authoritative."""

    def test_a_figure_absent_from_the_results_file_is_rejected(self) -> None:
        from forecaster.stages.i_narrative import _invented

        allowed = {"4.72", "47250", "0.9"}
        self.assertEqual(_invented("The forecast is 4.72 per share.", allowed), [])
        self.assertEqual(_invented("Net sales of 47250 million.", allowed), [])
        self.assertEqual(
            _invented("Margins should reach 61.4 per cent.", allowed),
            ["61.4"],
            "a figure the pipeline never produced must be caught",
        )

    def test_a_trailing_zero_is_the_same_figure(self) -> None:
        from forecaster.stages.i_narrative import _invented

        self.assertEqual(_invented("came in at 4.720", {"4.72"}), [])
