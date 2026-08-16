// Read the results files the pipeline wrote and fill the four supplied templates.
//
// This is the only place a forecast becomes a workbook, and it is deliberately
// dumb: it reads a number that Python already decided and writes it into a cell.
// It computes nothing. If a number needs deriving, it gets derived in the
// pipeline where a test can see it, not here.
//
// A missing number scores 5.0, which is the worst outcome available on this
// board — worse than any wrong number. So when a company's run failed, the
// previous accepted value is carried forward rather than left blank, and the
// carry-forward is reported loudly on stdout and recorded in the manifest. A
// degraded number that says it is degraded is the honest option; a blank is not.

import fs from "node:fs";
import path from "node:path";
import { fillWorkbook, readForecasts } from "./workbook.mjs";
import { pickRun } from "./run-health.mjs";

const root = path.resolve(import.meta.dirname, "..");
const definitions = JSON.parse(
  fs.readFileSync(path.join(root, "challenge", "companies.json"), "utf8"),
);

// The newest *healthy* run, not simply the newest. A run that degraded its way
// to a number must never overwrite one that did not.
function chooseResults(ticker) {
  const runsDir = path.join(root, "runs");
  if (!fs.existsSync(runsDir)) return { chosen: null, fallback: false, considered: [] };
  const prefix = `${ticker.replace(":", "_")}-`;
  const candidates = fs
    .readdirSync(runsDir)
    .filter((name) => name.startsWith(prefix))
    .map((name) => path.join(runsDir, name, "results.json"))
    .filter((file) => fs.existsSync(file))
    .map((file) => ({
      file,
      mtime: fs.statSync(file).mtimeMs,
      results: JSON.parse(fs.readFileSync(file, "utf8")),
    }));
  return pickRun(candidates);
}

function forecastFor(results, metricLabel) {
  const entry = results.forecast.metrics.find((m) => m.metric_label === metricLabel);
  if (!entry) return null;
  const value = entry.forecast;
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

const manifest = { generatedAt: new Date().toISOString(), companies: {} };
let carriedForward = 0;

for (const company of definitions.companies) {
  const outputPath = path.join(root, "submission", company.outputFile);
  const templatePath = path.join(root, "challenge", "templates", company.outputFile);
  const picked = chooseResults(company.ticker);
  const resultsPath = picked.chosen?.file ?? null;
  const results = picked.chosen?.results ?? null;

  if (picked.fallback && picked.chosen) {
    console.warn(
      `  ! ${company.ticker}: no healthy run available. Using ${path.basename(
        path.dirname(picked.chosen.file),
      )} anyway — ${picked.chosen.health.reasons.join("; ")}`,
    );
  } else if (picked.chosen && picked.considered[0]?.file !== picked.chosen.file) {
    console.warn(
      `  ! ${company.ticker}: the newest run was not healthy (${picked.considered[0].health.reasons.join(
        "; ",
      )}). Falling back to ${path.basename(path.dirname(picked.chosen.file))}.`,
    );
  }

  const existing = fs.existsSync(outputPath) ? readForecasts(outputPath) : new Map();

  const values = [];
  const provenance = [];

  for (const metric of company.metrics) {
    const fresh = results ? forecastFor(results, metric.label) : null;
    if (fresh !== null) {
      values.push(fresh);
      provenance.push({ metric: metric.label, value: fresh, source: "this run" });
      continue;
    }
    const previous = existing.get(metric.label);
    if (typeof previous === "number" && Number.isFinite(previous)) {
      values.push(previous);
      carriedForward += 1;
      provenance.push({ metric: metric.label, value: previous, source: "CARRIED FORWARD" });
      console.warn(
        `  ! ${company.ticker} "${metric.label}": no value from this run — carrying forward ${previous}`,
      );
      continue;
    }
    throw new Error(
      `${company.ticker} "${metric.label}": no forecast from this run and nothing to carry ` +
        `forward. Refusing to write a blank, which would score the maximum penalty.`,
    );
  }

  const written = fillWorkbook({ templatePath, outputPath, company, values });
  manifest.companies[company.ticker] = {
    results: resultsPath,
    runHealth: picked.chosen?.health ?? null,
    usedDegradedRun: picked.fallback,
    runsConsidered: picked.considered.map((c) => ({
      run: path.basename(path.dirname(c.file)),
      healthy: c.health.healthy,
      reasons: c.health.reasons,
    })),
    lastCompletedStage: results?.header?.last_completed_stage ?? null,
    costUsd: results?.cost?.spent_usd ?? null,
    provenance,
    cells: written.written,
  };
  console.log(
    `  ${company.ticker.padEnd(8)} ${company.outputFile.padEnd(20)} ${values
      .map((v) => String(v))
      .join("  ")}`,
  );
}

fs.writeFileSync(
  path.join(root, "submission", "manifest.json"),
  `${JSON.stringify(manifest, null, 2)}\n`,
  "utf8",
);

console.log(
  `\nWrote ${Object.keys(manifest.companies).length} workbooks.` +
    (carriedForward ? ` ${carriedForward} value(s) carried forward — see submission/manifest.json.` : ""),
);
