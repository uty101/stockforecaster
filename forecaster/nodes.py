"""The node registry: every node in the pipeline with its stable ID.

These IDs appear in module names, event names, log lines and on the System
sheet, so a line in the event tape identifies a node without ambiguity.

The registry is the single definition. The System sheet draws from it, the event
tape tags against it, and the gates panel counts against it, so a node that
exists in code but not here has no way to render and a node here with no code
shows as unbuilt rather than being quietly forgotten.

Where this build departs from the specification, the node stays and its
`available` flag says why. The four target companies come with a frozen
1,139-document corpus and no market data feed, so J2, J3, J4 and J6 have no
source behind them. Deleting them would hide that; keeping them dark is the
honest rendering, and it is what the Integrity sheet reports.
"""

from __future__ import annotations

from dataclasses import dataclass, field

SOURCE = "source"
ACQUIRE = "acquire"
DETERMINISTIC = "deterministic"
AGENT = "agent"
GATE = "gate"
OUTPUT = "output"

NONE_TIER = "none"
CHEAP = "cheap"
MID = "mid"
DEEP = "deep"


@dataclass(frozen=True)
class Node:
    id: str
    stage: str
    label: str
    kind: str
    tier: str = NONE_TIER
    available: bool = True
    note: str = ""
    agent: str = ""


def _n(*args, **kwargs) -> Node:
    return Node(*args, **kwargs)


# -- A, sources -------------------------------------------------------------
# The specification's J1..J7 are kept in place. Four of them have no source in
# this build and say so rather than disappearing.
SOURCES = [
    _n("J1", "A", "Frozen filing corpus", SOURCE,
       note="8-K/10-Q/10-K equivalents, transcripts and slides for the four target companies, "
            "frozen 2026-08-14. Stands in for SEC: markdown filings, no XBRL and no SIC peer list."),
    _n("J2", "A", "Market data", SOURCE, available=False,
       note="No yfinance or equivalent adapter. No analyst consensus, no prices, no share counts "
            "from a vendor. Share counts come from the filings instead; consensus does not exist."),
    _n("J3", "A", "News", SOURCE, available=False,
       note="No Exa or news feed. Analyst Q&A in the transcript sequence is the only coverage "
            "signal held, and E6 reads that instead."),
    _n("J4", "A", "Options chain", SOURCE, available=False,
       note="No LSE options feed, so no implied move."),
    _n("J5", "A", "Universe", SOURCE,
       note="challenge/companies.json: four companies, twelve metrics, prepared and local. "
            "Fixed by the organisers, so universe construction uses no post-as-of information "
            "by construction."),
    _n("J6", "A", "Macro", SOURCE, available=False,
       note="No FRED/ALFRED adapter, so no macro series and no realtime_start pinning. U20 works "
            "from macro commentary quoted inside the filings and calls."),
    _n("J7", "A", "Sponsor feed", SOURCE,
       note="challenge/templates: the metric label, unit and period header for each of the twelve "
            "numbers. A structured source: values are typed, not quoted prose."),
]

# -- B, acquire -------------------------------------------------------------
ACQUIRERS = [
    _n("U1", "B", "B1 numbers", ACQUIRE,
       note="Reported metric history per target metric. The consensus bus is tapped here; on this "
            "build the tap is empty and says so."),
    _n("U2", "B", "B1B series", ACQUIRE,
       note="Statement history as filed, held separately from the evidence store because the model "
            "needs an uninterrupted series."),
    _n("U3", "B", "B2 filings", ACQUIRE,
       note="Results releases selected by form token, this corpus's item-2.02 equivalent."),
    _n("U4", "B", "B5 extract", AGENT, tier=CHEAP, agent="extract_guidance"),
    _n("U5", "B", "B3 industry", ACQUIRE, available=False,
       note="No SIC peer list and no industry news. The only peers held are the other three "
            "target companies, which U19 uses and labels as such."),
    _n("U6", "B", "B4 macro", ACQUIRE, available=False, note="No macro adapter; see J6."),
    _n("U7", "B", "B6 bridge", AGENT, tier=CHEAP, agent="extract_bridge"),
    _n("U8", "B", "E7 calls", AGENT, tier=CHEAP, agent="scan_calls"),
    _n("U9", "B", "E6 perception", AGENT, tier=CHEAP, agent="scan_perception"),
]

# -- C through I ------------------------------------------------------------
PIPELINE = [
    _n("U10", "C", "Evidence store", DETERMINISTIC),
    _n("U11", "D", "Three-statement model", DETERMINISTIC),
    _n("U12", "E", "Mechanical", AGENT, tier=NONE_TIER, agent="lens_mechanical",
       note="No model call. Pure arithmetic, zero tokens."),
    _n("U13", "E", "Guidance", AGENT, tier=MID, agent="lens_guidance"),
    _n("U14", "E", "Drivers", AGENT, tier=MID, agent="lens_drivers"),
    _n("U15", "E", "Demand", AGENT, tier=MID, agent="lens_demand"),
    _n("U16", "E", "Market", AGENT, tier=MID, agent="lens_market"),
    _n("U17", "E", "Margins", AGENT, tier=MID, agent="lens_margins"),
    _n("U18", "E", "Forensics", AGENT, tier=MID, agent="lens_forensics"),
    _n("U19", "E", "Peer read", AGENT, tier=MID, agent="lens_peer_read"),
    _n("U20", "E", "Macro", AGENT, tier=MID, agent="lens_macro"),
    _n("U21", "V1", "Reconcile", GATE),
    _n("U22", "F", "Champion", AGENT, tier=MID, agent="champion"),
    _n("U23", "G", "Judge", AGENT, tier=DEEP, agent="judge"),
    _n("U24", "V2", "Comparability", AGENT, tier=CHEAP, agent="comparability"),
    _n("U25", "V3", "Calibrate", DETERMINISTIC),
    _n("U26", "H", "Position", DETERMINISTIC),
    _n("OUT", "I", "Output", OUTPUT, tier=MID, agent="narrative"),
]

ALL: list[Node] = SOURCES + ACQUIRERS + PIPELINE
BY_ID: dict[str, Node] = {node.id: node for node in ALL}

LENS_NODES = [node.id for node in PIPELINE if node.stage == "E"]


def node(node_id: str) -> Node:
    if node_id not in BY_ID:
        raise KeyError(f"unknown node {node_id!r}; add it to forecaster/nodes.py rather than inventing an ID")
    return BY_ID[node_id]


def agents() -> list[Node]:
    return [item for item in ALL if item.kind == AGENT or item.kind == OUTPUT]


def unavailable() -> list[Node]:
    return [item for item in ALL if not item.available]


def summary() -> list[dict[str, object]]:
    """What the System sheet and the gates panel read."""
    return [
        {
            "id": item.id,
            "stage": item.stage,
            "label": item.label,
            "kind": item.kind,
            "tier": item.tier,
            "available": item.available,
            "note": item.note,
            "agent": item.agent,
        }
        for item in ALL
    ]
