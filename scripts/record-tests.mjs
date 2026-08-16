// Run the checks and record the result, so the gates panel can state how many
// pass rather than assert that they do.
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";

const root = path.resolve(import.meta.dirname, "..");
// The summary goes to stderr whether the run passes or fails, so both streams
// are read. Reading only stdout records a clean run as no run at all.
const run = spawnSync("py", ["-m", "unittest", "discover", "-s", "tests", "-t", ".", "-q"], {
  cwd: root,
  encoding: "utf8",
});
const out = `${run.stdout ?? ""}${run.stderr ?? ""}`;
const code = run.status ?? 1;

const ran = Number((out.match(/Ran (\d+) test/) ?? [])[1] ?? 0);
const failures = Number((out.match(/failures=(\d+)/) ?? [])[1] ?? 0);
const errors = Number((out.match(/errors=(\d+)/) ?? [])[1] ?? 0);
const failed = failures + errors;

const report = {
  total: ran,
  passed: ran - failed,
  failed,
  ranAt: new Date().toISOString(),
  ok: code === 0,
};
fs.mkdirSync(path.join(root, "runs"), { recursive: true });
fs.writeFileSync(path.join(root, "runs", "_tests.json"), JSON.stringify(report, null, 1), "utf8");
console.log(`checks: ${report.passed}/${report.total} pass`);
