// A degraded run must never overwrite a healthy one.
//
// This is a regression test for a real incident: the API ran out of credit
// partway through a run, eight of Deere's nine lenses failed, and the pipeline
// correctly degraded to a number. That number then replaced a healthy run's
// number in the workbook, because the writer took the newest run rather than the
// best one.

import assert from "node:assert/strict";
import test from "node:test";
import { health, pickRun, MIN_SURVIVING_LENSES } from "../src/run-health.mjs";

const healthy = {
  header: { last_completed_stage: "I" },
  reconciliation: { surviving: Array(9).fill("lens") },
  lenses: { counts: { ran: 9, failed: 0 } },
  judge: { degraded: false },
  forecast: { metrics: [{ metric_label: "Revenue", consensus: 10731.5, forecast: 12350 }] },
};

const degraded = {
  header: { last_completed_stage: "I" },
  reconciliation: { surviving: ["lens_mechanical"] },
  lenses: { counts: { ran: 9, failed: 8 } },
  judge: { degraded: false },
  forecast: { metrics: [{ metric_label: "Revenue", consensus: 10731.5, forecast: 10731.5 }] },
};

test("a run with almost every lens dead is not healthy", () => {
  const verdict = health(degraded);
  assert.equal(verdict.healthy, false);
  assert.ok(verdict.reasons.some((r) => r.includes(`floor of ${MIN_SURVIVING_LENSES}`)));
  assert.ok(verdict.reasons.some((r) => r.includes("failed to run")));
});

test("a forecast equal to consensus is the positioning fallback, not a forecast", () => {
  assert.ok(health(degraded).reasons.some((r) => r.includes("fell back to the consensus figure")));
});

test("a complete run with every lens surviving is healthy", () => {
  assert.deepEqual(health(healthy), { healthy: true, reasons: [], surviving: 9, ran: 9, stage: "I" });
});

test("a run that stopped before the end is not healthy", () => {
  const short = { ...healthy, header: { last_completed_stage: "E" } };
  assert.ok(health(short).reasons.some((r) => r.includes("stopped after stage E")));
});

test("the newer degraded run loses to the older healthy one", () => {
  const picked = pickRun([
    { file: "old/results.json", mtime: 100, results: healthy },
    { file: "new/results.json", mtime: 200, results: degraded },
  ]);
  assert.equal(picked.chosen.file, "old/results.json");
  assert.equal(picked.fallback, false);
});

test("when nothing is healthy the newest is used and flagged", () => {
  const picked = pickRun([
    { file: "a/results.json", mtime: 100, results: degraded },
    { file: "b/results.json", mtime: 200, results: degraded },
  ]);
  assert.equal(picked.chosen.file, "b/results.json");
  assert.equal(picked.fallback, true, "a blank scores worse than a labelled degraded number");
});
