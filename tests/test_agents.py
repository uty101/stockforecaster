"""Agent framework tests.

These are the Part 11 tests that live at the framework level: the cost ceiling
asserted on call count rather than on the ledger, exactly one deep-tier call per
run, a prompt edit changing the cache key, the cheap tier never receiving effort,
and the schema layer failing closed.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from forecaster.agents import schema
from forecaster.agents.cache import ResponseCache
from forecaster.agents.client import (
    Client,
    DeepCallBudgetExceeded,
    ModelCallFailed,
    cacheable,
    plain,
)
from forecaster.agents.prompts import PromptError, PromptStore
from forecaster.agents.runner import build_system_blocks, fan_out, failures, successes
from forecaster.agents.schema import SchemaError
from forecaster.agents.tiers import TierRouter
from forecaster.events import NullSink
from forecaster.ledger import CostCeilingExceeded, CostLedger

MODELS = {
    "cheap": "claude-haiku-4-5-20251001",
    "mid": "claude-sonnet-5",
    "deep": "claude-opus-5",
}

SIMPLE_SCHEMA = schema.obj(
    {
        "eps": schema.nullable("number", "EPS estimate, or null when abstaining"),
        "note": schema.text("one sentence"),
    }
)


class RecordingClient(Client):
    """A client whose transport is a list of canned responses.

    Every request body is recorded, so a test can assert what was sent -- which
    is the only way to check that effort never reaches the cheap tier.
    """

    def __init__(self, *args: Any, replies: list[dict[str, Any]] | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.sent: list[dict[str, Any]] = []
        self.replies = replies or []

    def _post(self, body: dict[str, Any], **context: Any) -> dict[str, Any]:
        self.sent.append(body)
        if self.replies:
            return self.replies.pop(0)
        return reply({"eps": 1.5, "note": "ok"})


def reply(value: dict[str, Any], *, input_tokens: int = 1000, output_tokens: int = 200) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": json.dumps(value)}],
        "stop_reason": "end_turn",
        "usage": {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
        },
    }


def build_client(ceiling_usd: float = 25.0, **kwargs: Any) -> RecordingClient:
    return RecordingClient(
        router=TierRouter(MODELS),
        ledger=CostLedger(ceiling_usd=ceiling_usd),
        cache=ResponseCache(Path(tempfile.mkdtemp(prefix="forecaster-cache-"))),
        events=NullSink(),
        api_key="test-key",
        **kwargs,
    )


def make_call(client: Client, **overrides: Any) -> Any:
    kwargs: dict[str, Any] = {
        "stage": "E",
        "node": "U13",
        "agent": "lens_guidance",
        "tier_name": "mid",
        "system_blocks": [plain("shared corpus"), plain("your question")],
        "user_text": "Forecast the quarter.",
        "response_schema": SIMPLE_SCHEMA,
        "max_tokens": 4000,
        "effort": "high",
        "prompt_version": "1",
    }
    kwargs.update(overrides)
    return client.call(**kwargs)


class TierGating(unittest.TestCase):
    def test_the_cheap_tier_never_receives_effort_or_thinking(self) -> None:
        """Haiku 4.5 rejects both with a hard 400, and every extraction runs there."""
        client = build_client()
        make_call(client, tier_name="cheap", effort="high")

        body = client.sent[0]
        self.assertEqual(body["model"], "claude-haiku-4-5-20251001")
        self.assertNotIn("thinking", body)
        self.assertNotIn("effort", body["output_config"])

    def test_a_dated_snapshot_id_is_gated_the_same_as_its_alias(self) -> None:
        """Gate on prefix, never on an exact model string."""
        router = TierRouter(MODELS)
        self.assertFalse(router["cheap"].supports_effort)
        self.assertTrue(router["mid"].supports_effort)
        self.assertTrue(router["deep"].supports_thinking)

    def test_capable_tiers_receive_thinking_and_effort(self) -> None:
        client = build_client()
        make_call(client, tier_name="deep", effort="high")

        body = client.sent[0]
        self.assertEqual(body["thinking"], {"type": "adaptive"})
        self.assertEqual(body["output_config"]["effort"], "high")

    def test_the_cache_minimum_differs_by_family(self) -> None:
        """Below the minimum nothing caches and no error is raised."""
        router = TierRouter(MODELS)
        self.assertEqual(router["cheap"].cache_minimum_tokens, 4096)
        self.assertEqual(router["mid"].cache_minimum_tokens, 1024)
        self.assertEqual(router["deep"].cache_minimum_tokens, 512)


class CostCeiling(unittest.TestCase):
    def test_the_ceiling_raises_before_the_breaching_call(self) -> None:
        """Asserted on call count, not on the ledger. A ledger checked after the
        fact is not a ceiling."""
        client = build_client(ceiling_usd=0.001)

        with self.assertRaises(CostCeilingExceeded):
            make_call(client, tier_name="deep")

        self.assertEqual(len(client.sent), 0, "the breaching call must never be sent")

    def test_exactly_one_deep_call_per_run(self) -> None:
        client = build_client()
        make_call(client, tier_name="deep", node="U23", agent="judge")

        with self.assertRaises(DeepCallBudgetExceeded):
            make_call(client, tier_name="deep", node="U23", agent="judge", user_text="again")

        self.assertEqual(len(client.sent), 1)
        self.assertEqual(client.ledger.deep_calls, 1)

    def test_cost_is_recorded_per_call_with_the_token_split(self) -> None:
        client = build_client()
        make_call(client)

        record = client.ledger.calls[0]
        self.assertEqual(record.tier, "mid")
        self.assertEqual(record.input_tokens, 1000)
        self.assertEqual(record.output_tokens, 200)
        self.assertAlmostEqual(record.usd, 1000 * 3.0 / 1e6 + 200 * 15.0 / 1e6)
        self.assertIn("E", client.ledger.per_stage())


class Caching(unittest.TestCase):
    def test_a_second_identical_call_is_served_from_cache(self) -> None:
        client = build_client()
        first = make_call(client)
        second = make_call(client)

        self.assertFalse(first.cached)
        self.assertTrue(second.cached)
        self.assertEqual(len(client.sent), 1, "a cache hit must not reach the network")

    def test_editing_prompt_text_changes_the_cache_key(self) -> None:
        client = build_client()
        make_call(client, user_text="Forecast the quarter.")
        make_call(client, user_text="Forecast the quarter, carefully.")

        self.assertEqual(len(client.sent), 2, "an edited prompt must invalidate its old answer")

    def test_changing_the_prompt_version_alone_changes_the_key(self) -> None:
        client = build_client()
        make_call(client, prompt_version="1")
        make_call(client, prompt_version="2")

        self.assertEqual(len(client.sent), 2)


class SchemaLayer(unittest.TestCase):
    def test_a_string_where_a_number_was_declared_fails_rather_than_coercing(self) -> None:
        with self.assertRaises(SchemaError) as raised:
            schema.validate({"eps": "1.5", "note": "n"}, SIMPLE_SCHEMA)
        self.assertIn("eps", str(raised.exception))

    def test_a_missing_field_is_not_defaulted(self) -> None:
        with self.assertRaises(SchemaError):
            schema.validate({"note": "n"}, SIMPLE_SCHEMA)

    def test_an_unexpected_field_fails_closed(self) -> None:
        with self.assertRaises(SchemaError):
            schema.validate({"eps": 1.0, "note": "n", "extra": True}, SIMPLE_SCHEMA)

    def test_null_is_accepted_only_where_declared_nullable(self) -> None:
        schema.validate({"eps": None, "note": "abstaining"}, SIMPLE_SCHEMA)
        with self.assertRaises(SchemaError):
            schema.validate({"eps": 1.0, "note": None}, SIMPLE_SCHEMA)

    def test_a_boolean_is_never_a_number(self) -> None:
        with self.assertRaises(SchemaError):
            schema.validate({"eps": True, "note": "n"}, SIMPLE_SCHEMA)

    def test_the_client_validates_before_returning(self) -> None:
        client = build_client(replies=[reply({"eps": "not a number", "note": "n"})])
        with self.assertRaises(SchemaError):
            make_call(client)


class Truncation(unittest.TestCase):
    def test_a_truncated_response_names_truncation_rather_than_a_parse_error(self) -> None:
        truncated = reply({"eps": 1.0, "note": "n"})
        truncated["stop_reason"] = "max_tokens"
        client = build_client(replies=[truncated])

        with self.assertRaises(ModelCallFailed) as raised:
            make_call(client)
        self.assertIn("max_tokens", str(raised.exception))


class Prompts(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = Path(tempfile.mkdtemp(prefix="forecaster-prompts-"))
        (self.directory / "lens_guidance.md").write_text(
            '---\nname: "lens_guidance"\nversion: "3"\ntier: "mid"\n---\n\n'
            "Read the guided range for {{period_noun}} and say what it implies.\n",
            encoding="utf-8",
        )
        self.store = PromptStore(self.directory)

    def test_a_prompt_carries_its_version_for_the_results_file(self) -> None:
        prompt = self.store.get("lens_guidance")
        self.assertEqual(prompt.version, "3")
        self.assertEqual(self.store.versions(), {"lens_guidance": "3"})

    def test_rendering_requires_every_placeholder(self) -> None:
        prompt = self.store.get("lens_guidance")
        self.assertIn("financial year", prompt.render(period_noun="financial year"))
        with self.assertRaises(PromptError):
            prompt.render()

    def test_a_value_with_no_placeholder_is_an_error_not_a_silent_drop(self) -> None:
        prompt = self.store.get("lens_guidance")
        with self.assertRaises(PromptError):
            prompt.render(period_noun="quarter", consensus=4.5)

    def test_a_missing_prompt_file_raises(self) -> None:
        with self.assertRaises(PromptError):
            self.store.get("does_not_exist")


class FanOut(unittest.TestCase):
    def test_the_shared_corpus_always_precedes_the_per_agent_text(self) -> None:
        """If the agent-specific text leads, every agent presents a different
        prefix and there is nothing to match."""
        router = TierRouter(MODELS)
        blocks = build_system_blocks("corpus " * 5000, "your question", router["mid"])

        self.assertTrue(blocks[0]["text"].startswith("corpus"))
        self.assertTrue(blocks[1]["text"].startswith("your question"))
        self.assertEqual(blocks[0].get("cache_control"), {"type": "ephemeral"})

    def test_the_house_punctuation_rule_reaches_every_agent(self) -> None:
        """Anything an agent writes can be rendered on a sheet, and the sheets do
        not repair the text they are given."""
        router = TierRouter(MODELS)
        blocks = build_system_blocks("corpus " * 5000, "your question", router["mid"])

        self.assertIn("no dash may join", blocks[1]["text"])
        # It must not sit in the cached prefix, which is shared and must not move.
        self.assertNotIn("Punctuation", blocks[0]["text"])

    def test_a_prefix_below_the_cache_minimum_is_not_marked(self) -> None:
        router = TierRouter(MODELS)
        block = cacheable("short", router["cheap"])
        self.assertNotIn("cache_control", block)

    def test_the_first_item_completes_before_the_rest_start(self) -> None:
        order: list[str] = []

        def worker(item: str) -> str:
            order.append(item)
            return item.upper()

        events = NullSink()
        results = fan_out(["a", "b", "c"], worker, events=events, max_workers=4)

        self.assertEqual(order[0], "a", "a cold parallel fan-out writes nine cache entries and reads none")
        self.assertEqual(sorted(results), ["A", "B", "C"])
        names = [record["event"] for record in events.records]
        self.assertEqual(names[:2], ["fan_out_warm_up", "fan_out_warmed"])

    def test_one_failure_does_not_take_the_others_down(self) -> None:
        def worker(item: str) -> str:
            if item == "b":
                raise ValueError("lens b exploded")
            return item.upper()

        results = fan_out(["a", "b", "c"], worker, events=NullSink())

        self.assertEqual(len(failures(results)), 1)
        self.assertEqual(sorted(successes(results)), ["A", "C"])


class Isolation(unittest.TestCase):
    def test_no_stage_module_talks_to_the_network_directly(self) -> None:
        """Every model call goes through the one client. A stage that reaches for
        the transport itself is outside the cost ceiling and the cache."""
        from forecaster.config import REPO_ROOT

        offenders = []
        for path in (REPO_ROOT / "forecaster" / "stages").glob("*.py"):
            text = path.read_text(encoding="utf-8")
            for banned in ("import urllib", "import anthropic", "from anthropic"):
                if banned in text:
                    offenders.append(f"{path.name}: {banned}")

        self.assertEqual(offenders, [], "stages must call models only through forecaster.agents.client")


if __name__ == "__main__":
    unittest.main()
