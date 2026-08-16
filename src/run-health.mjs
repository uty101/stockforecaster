// Is a completed run healthy enough for its numbers to be submitted?
//
// This exists because of a specific failure. The API ran out of credit partway
// through a run; Deere's research scout, all three scanners and eight of its
// nine lenses failed, and the pipeline did what it is supposed to do -- it
// degraded, labelled everything, and produced numbers anyway. Those numbers then
// overwrote a healthy run's numbers in the workbook, because the writer took the
// most recent run rather than the best one.
//
// Degrading rather than stopping is right: a blank scores the maximum penalty.
// Letting a degraded number silently replace a good one is not. So health is
// judged here, the newest *healthy* run wins, and when nothing is healthy the
// writer says so loudly rather than quietly shipping the wreckage.

export const MIN_SURVIVING_LENSES = 4;

export function health(results) {
  const reasons = [];

  const stage = results?.header?.last_completed_stage;
  if (stage !== "I") reasons.push(`stopped after stage ${stage ?? "unknown"}`);

  const surviving = results?.reconciliation?.surviving?.length ?? 0;
  const ran = results?.lenses?.counts?.ran ?? 0;
  if (surviving < MIN_SURVIVING_LENSES) {
    reasons.push(`only ${surviving} of ${ran} lenses survived, below the floor of ${MIN_SURVIVING_LENSES}`);
  }

  const failed = results?.lenses?.counts?.failed ?? 0;
  if (failed > 0) reasons.push(`${failed} lens${failed === 1 ? "" : "es"} failed to run at all`);

  // A judge that fell back to the median of surviving views is not a judgement.
  if (results?.judge?.degraded) reasons.push("the judge degraded to its fallback");

  // Any metric whose forecast simply equals consensus is the positioning
  // fallback firing, which means nothing upstream produced an own estimate.
  const fellBack = (results?.forecast?.metrics ?? []).filter(
    (m) => typeof m.consensus === "number" && m.forecast === m.consensus,
  );
  if (fellBack.length) {
    reasons.push(
      `${fellBack.length} metric(s) fell back to the consensus figure: ${fellBack
        .map((m) => m.metric_label)
        .join(", ")}`,
    );
  }

  return { healthy: reasons.length === 0, reasons, surviving, ran, stage };
}

// Pick the run whose numbers should be submitted: the newest healthy one, and
// only if there is none, the newest of any kind -- flagged.
export function pickRun(candidates) {
  const scored = candidates
    .map((c) => ({ ...c, health: health(c.results) }))
    .sort((a, b) => b.mtime - a.mtime);

  const healthy = scored.find((c) => c.health.healthy);
  if (healthy) return { chosen: healthy, fallback: false, considered: scored };

  return { chosen: scored[0] ?? null, fallback: true, considered: scored };
}
