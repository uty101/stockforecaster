// Collect the newest results file and event tape for each company into one
// bundle the site reads.
//
// This is deliberately the only place the site touches the run directories, and
// it copies rather than computes. Every number on every sheet comes out of a
// results file that Python wrote; if a sheet needs something that is not here,
// the fix is a field in results.json, not a calculation in the site.

import fs from "node:fs";
import path from "node:path";

const root = path.resolve(import.meta.dirname, "..");
const runsDir = path.join(root, "runs");
const outDir = path.join(import.meta.dirname, "src", "data");
const publicDir = path.join(import.meta.dirname, "public", "data");

fs.mkdirSync(outDir, { recursive: true });
fs.mkdirSync(publicDir, { recursive: true });

const definitions = JSON.parse(
  fs.readFileSync(path.join(root, "challenge", "companies.json"), "utf8"),
);

function newestRun(ticker) {
  if (!fs.existsSync(runsDir)) return null;
  const prefix = `${ticker.replace(":", "_")}-`;
  const dirs = fs
    .readdirSync(runsDir)
    .filter((n) => n.startsWith(prefix))
    .map((n) => path.join(runsDir, n))
    .filter((d) => fs.existsSync(path.join(d, "results.json")))
    .map((d) => ({ d, m: fs.statSync(path.join(d, "results.json")).mtimeMs }))
    .sort((a, b) => b.m - a.m);
  return dirs.length ? dirs[0].d : null;
}

function readTape(dir) {
  const file = path.join(dir, "events.jsonl");
  if (!fs.existsSync(file)) return [];
  return fs
    .readFileSync(file, "utf8")
    .split("\n")
    .filter((line) => line.trim())
    .map((line) => {
      try {
        return JSON.parse(line);
      } catch {
        return null;
      }
    })
    .filter(Boolean);
}

const companies = [];
for (const company of definitions.companies) {
  const dir = newestRun(company.ticker);
  if (!dir) {
    companies.push({ ticker: company.ticker, missing: true, definition: company });
    continue;
  }
  const results = JSON.parse(fs.readFileSync(path.join(dir, "results.json"), "utf8"));
  const tape = readTape(dir);
  companies.push({ ticker: company.ticker, definition: company, results, tape, runDir: path.basename(dir) });
  fs.writeFileSync(
    path.join(publicDir, `${company.ticker.replace(":", "_")}-tape.json`),
    JSON.stringify(tape),
    "utf8",
  );
}

// The prompt directory is the roster. The Agents sheet is generated from it so a
// new prompt without a roster entry cannot silently exist.
const promptDir = path.join(root, "llm", "prompts");
const prompts = fs.existsSync(promptDir)
  ? fs.readdirSync(promptDir).filter((f) => f.endsWith(".md")).map((file) => {
      const text = fs.readFileSync(path.join(promptDir, file), "utf8");
      const meta = {};
      const block = text.split("---")[1] || "";
      for (const line of block.split("\n")) {
        const [k, ...rest] = line.split(":");
        if (!k || !rest.length) continue;
        meta[k.trim()] = rest.join(":").trim().replace(/^"|"$/g, "");
      }
      return { file, ...meta, body: text.split("---").slice(2).join("---").trim() };
    })
  : [];

// Which parts of the run have a check written against their failure modes.
// Read by scanning the test files for the module each part is implemented in,
// so the fraction on the gates panel cannot be stale or asserted.
const MODULE_OF = {
  A: "a_sources",
  C: "c_structure",
  D: "d_model",
  E: "e_analyse",
  V1: "v1_reconcile",
  F: "f_challenge",
  G: "g_judge",
  V2: "v2_comparability",
  V3: "h_position",
  H: "h_position",
  I: "i_output",
};
const MODULE_BY_NODE = {
  U1: "b_acquire",
  U2: "b_acquire",
  U3: "b_acquire",
  U4: "b5_extract",
  U5: "b3_research",
  U6: "b_acquire",
  U7: "b5_extract",
  U8: "b_scan",
  U9: "b_scan",
};

const testDir = path.join(root, "tests");
const testFiles = fs.existsSync(testDir)
  ? fs.readdirSync(testDir).filter((f) => f.startsWith("test_") && f.endsWith(".py"))
  : [];
const testText = testFiles.map((f) => fs.readFileSync(path.join(testDir, f), "utf8")).join("\n");
const testCount = (testText.match(/def test_/g) ?? []).length;

const exercised = new Set();
for (const name of new Set([...Object.values(MODULE_OF), ...Object.values(MODULE_BY_NODE)])) {
  if (new RegExp(`\\b${name}\\b`).test(testText)) exercised.add(name);
}

const registry = companies.find((c) => c.results)?.results?.nodes ?? [];
const nodesWithCoverage = registry.map((node) => {
  const module = MODULE_BY_NODE[node.id] ?? MODULE_OF[node.stage] ?? null;
  return { ...node, module, covered: Boolean(module && exercised.has(module)) };
});

// The check results themselves, if a run of them has been recorded.
let tests = { total: testCount, passed: null, failed: null, ranAt: null };
const testReport = path.join(root, "runs", "_tests.json");
if (fs.existsSync(testReport)) {
  try {
    tests = { ...tests, ...JSON.parse(fs.readFileSync(testReport, "utf8")) };
  } catch {
    /* an unreadable report leaves the panel saying so */
  }
}

const bundle = {
  generatedAt: new Date().toISOString(),
  companies,
  prompts,
  nodes: nodesWithCoverage,
  tests,
};

fs.writeFileSync(path.join(outDir, "bundle.json"), JSON.stringify(bundle, null, 1), "utf8");
console.log(
  `bundle: ${companies.filter((c) => !c.missing).length}/${companies.length} companies, ` +
    `${prompts.length} prompts, ${companies.reduce((n, c) => n + (c.tape?.length ?? 0), 0)} events`,
);
