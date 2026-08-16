// Reading helpers over the bundle. These select and count what the run wrote.
// They never invent a figure: if a field is absent the caller renders the
// absence rather than a substitute.

import bundle from "../data/bundle.json";

export const companies = bundle.companies.filter((c) => !c.missing && c.results);
export const missing = bundle.companies.filter((c) => c.missing || !c.results);
export const prompts = bundle.prompts ?? [];
export const nodes = bundle.nodes ?? [];
export const generatedAt = bundle.generatedAt;
export const coverage = bundle.coverage ?? null;
export const testRun = bundle.tests ?? null;

export const primary = companies[0] ?? null;

export function tickerId(ticker) {
  return String(ticker).replace(/[^A-Za-z0-9]/g, "_");
}

export function header(company) {
  return company?.results?.header ?? {};
}

export const anyFixture = companies.some((c) => header(c).fixture);
export const incomplete = companies.filter(
  (c) => header(c).last_completed_stage && header(c).last_completed_stage !== "I",
);

export const lockDate = header(primary).as_of ?? "—";
export const runLabel = header(primary).run_id ?? "—";

// Stage letters in the order the run walks them.
export const STAGES = [
  ["A", "sources"],
  ["B", "acquire"],
  ["C", "evidence"],
  ["D", "history"],
  ["E", "workstreams"],
  ["V1", "citations"],
  ["F", "challenge"],
  ["G", "weighting"],
  ["V2", "comparability"],
  ["V3", "calibration"],
  ["H", "position"],
  ["I", "output"],
];

export function sum(list, pick) {
  return list.reduce((n, item) => n + (pick(item) ?? 0), 0);
}

// Cost is recorded per call. Everything the cost sheet shows is a sum of these.
export function callsOf(company) {
  return company?.results?.cost?.calls ?? [];
}

export function costByAgent(company) {
  const out = new Map();
  for (const call of callsOf(company)) {
    const key = call.agent;
    const row = out.get(key) ?? {
      agent: key,
      tier: call.tier,
      model: call.model,
      calls: 0,
      input: 0,
      output: 0,
      cacheWrite: 0,
      cacheRead: 0,
      usd: 0,
      duration: 0,
    };
    row.calls += 1;
    row.input += call.input_tokens ?? 0;
    row.output += call.output_tokens ?? 0;
    row.cacheWrite += call.cache_write_tokens ?? 0;
    row.cacheRead += call.cache_read_tokens ?? 0;
    row.usd += call.usd ?? 0;
    row.duration += call.duration_s ?? 0;
    out.set(key, row);
  }
  return [...out.values()].sort((a, b) => b.usd - a.usd);
}

export function costByStage(company) {
  const out = new Map();
  for (const call of callsOf(company)) {
    const key = call.stage;
    const row = out.get(key) ?? {
      stage: key,
      calls: 0,
      input: 0,
      output: 0,
      cacheWrite: 0,
      cacheRead: 0,
      usd: 0,
    };
    row.calls += 1;
    row.input += call.input_tokens ?? 0;
    row.output += call.output_tokens ?? 0;
    row.cacheWrite += call.cache_write_tokens ?? 0;
    row.cacheRead += call.cache_read_tokens ?? 0;
    row.usd += call.usd ?? 0;
    out.set(key, row);
  }
  return [...out.values()].sort((a, b) => b.usd - a.usd);
}

// The badges under each box on the flow drawing.
export function nodeLoad(company) {
  const byAgent = new Map(costByAgent(company).map((r) => [r.agent, r]));
  const out = new Map();
  for (const node of nodes) {
    const row = node.agent ? byAgent.get(node.agent) : null;
    out.set(node.id, {
      tokens: row ? row.input + row.output + row.cacheRead + row.cacheWrite : null,
      seconds: row ? row.duration : null,
      usd: row ? row.usd : null,
      calls: row ? row.calls : 0,
    });
  }
  return out;
}

// Which workstreams reached the weighting, and with what.
export function lensRows(company) {
  const views = company?.results?.lenses?.views ?? [];
  const dropped = new Set(company?.results?.reconciliation?.dropped ?? []);
  return views.map((view) => {
    const estimates = view.estimates ?? [];
    const abstained = estimates.length > 0 && estimates.every((e) => e.estimate === null);
    return {
      ...view,
      abstained,
      dropped: view.dropped || dropped.has(view.lens),
      state: view.dropped || dropped.has(view.lens) ? "dropped" : abstained ? "abstained" : "pass",
      citations: estimates.reduce((n, e) => n + (e.citations?.length ?? 0), 0),
    };
  });
}

export function metricOf(company, label) {
  return (company?.results?.forecast?.metrics ?? []).find((m) => m.metric_label === label) ?? null;
}

export function claimsFor(company) {
  return company?.results?.evidence?.claims ?? [];
}

export function documentsFor(company) {
  return company?.results?.evidence?.documents ?? {};
}

// The gate rows. Each one reads its state out of the run rather than carrying a
// written verdict, so a gate cannot claim to have passed on a run where it did
// not.
export function gates(company) {
  const r = company?.results ?? {};
  const rec = r.reconciliation ?? {};
  const method = r.method ?? {};
  const model = r.model ?? {};
  const rate = rec.verification_rate;
  const reproduced = (model.metrics ?? []).filter((m) => typeof m.reproduction_error === "number");
  const worst = reproduced.length
    ? reproduced.reduce((a, b) => (Math.abs(a.reproduction_error) > Math.abs(b.reproduction_error) ? a : b))
    : null;

  return [
    {
      state: rate === 1 ? "pass" : "open",
      title: "Citations match the filings",
      detail:
        rate === 1
          ? `${rec.citations_matched} of ${rec.citations_checked} quoted figures found in the document they cite.`
          : `${rec.citations_matched} of ${rec.citations_checked} quoted figures found in the document they cite.`,
      blocks: rate === 1 ? null : "Any figure resting on an unmatched quote.",
    },
    {
      state: (r.comparability?.flags ?? []).length > 0 ? "pass" : "open",
      title: "Comparability checked",
      detail: r.comparability?.any_fired
        ? `${(r.comparability.fired ?? []).length} of ${(r.comparability.flags ?? []).length} checks fired against the prior year base.`
        : `${(r.comparability?.flags ?? []).length} checks run against the prior year base, none fired.`,
      blocks: (r.comparability?.flags ?? []).length > 0 ? null : "Reading growth against a base that moved.",
    },
    {
      state: worst ? "pass" : "open",
      title: "History reproduces",
      detail: worst
        ? `Widest error against a reported period is ${worst.reproduction_error > 0 ? "+" : "-"}${Math.abs(worst.reproduction_error).toFixed(2)}% on ${worst.metric}.`
        : "No reported period has been reproduced from history alone.",
      blocks: worst ? null : "Trusting a projection from a model whose error is unknown.",
    },
    {
      state: (method.backtest?.runs ?? 0) > 0 ? "pass" : "open",
      title: "Skill against the Street",
      detail:
        method.backtest?.why ??
        "Scoring the estimate against the Street bar as it stood at past quarters needs a consensus history.",
      blocks: (method.backtest?.runs ?? 0) > 0 ? null : "Any claim that these estimates beat the Street.",
    },
    {
      state: method.beta_measured ? "pass" : "open",
      title: "Position sizing calibrated",
      detail: method.beta_measured
        ? "The multiplier on each condition is fitted to realised outcomes."
        : "The multiplier on each condition is set from the stated preset, not fitted to realised outcomes.",
      blocks: method.beta_measured ? null : "Reading the position size as a measured edge.",
    },
  ];
}
