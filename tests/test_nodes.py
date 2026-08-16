"""The node registry is the single definition of the pipeline's shape.

The Agents sheet is generated from the prompt files rather than a hand-kept
list, and these tests are the other half of that: the registry and the roster
have to agree on the count and the tiering, or the sheet confidently describes a
system that is not running.
"""

from __future__ import annotations

import unittest

from forecaster import nodes


class Registry(unittest.TestCase):
    def test_every_node_id_is_unique(self) -> None:
        ids = [item.id for item in nodes.ALL]
        self.assertEqual(len(ids), len(set(ids)))

    def test_the_roster_is_eighteen_agents_tiered_by_leverage(self) -> None:
        roster = nodes.agents()
        by_tier: dict[str, int] = {}
        for item in roster:
            by_tier[item.tier] = by_tier.get(item.tier, 0) + 1

        self.assertEqual(len(roster), 18)
        self.assertEqual(by_tier[nodes.NONE_TIER], 1, "exactly one agent has no model")
        self.assertEqual(by_tier[nodes.CHEAP], 6)
        self.assertEqual(by_tier[nodes.MID], 10)
        self.assertEqual(by_tier[nodes.DEEP], 1, "exactly one call runs on the deep tier")

    def test_there_are_nine_lenses_and_one_has_no_model(self) -> None:
        lenses = [item for item in nodes.ALL if item.stage == "E"]
        self.assertEqual(len(lenses), 9)
        self.assertEqual(
            [item.id for item in lenses if item.tier == nodes.NONE_TIER],
            ["U12"],
            "Mechanical is the lens still standing when the API is throttled",
        )

    def test_every_agent_node_names_a_prompt(self) -> None:
        for item in nodes.agents():
            if item.tier == nodes.NONE_TIER:
                continue
            self.assertTrue(item.agent, f"{item.id} has a tier but no prompt name")

    def test_unavailable_sources_stay_in_the_registry_with_a_reason(self) -> None:
        """Deleting a node we cannot feed would hide the gap. Keeping it dark is
        what the Integrity sheet reports. J4 has no entitlement on the available
        options plan; J6/U6 have no macro adapter."""
        dark = {item.id: item.note for item in nodes.unavailable()}
        self.assertEqual(sorted(dark), ["J4", "J6", "U6"])
        for node_id, note in dark.items():
            self.assertTrue(note, f"{node_id} is unavailable with no stated reason")

    def test_the_universe_and_sponsor_feed_are_live(self) -> None:
        self.assertTrue(nodes.node("J5").available)
        self.assertTrue(nodes.node("J7").available)
        self.assertTrue(nodes.node("J1").available)

    def test_an_unknown_id_raises_rather_than_being_invented(self) -> None:
        with self.assertRaises(KeyError):
            nodes.node("U99")


if __name__ == "__main__":
    unittest.main()
