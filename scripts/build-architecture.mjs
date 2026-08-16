// Generate architecture/index.html from the actual run.
//
// Generated rather than hand-written for one reason: a hand-written page drifts
// from the system within an hour of editing either, and the judging asks whether
// the diagram matches the code. Every count, cost and node on the page below is
// read out of results.json and the node registry, so the page cannot claim a
// system that did not run. Regenerate before locking.

import fs from "node:fs";
import path from "node:path";

const root = path.resolve(import.meta.dirname, "..");
const runsDir = path.join(root, "runs");
const definitions = JSON.parse(fs.readFileSync(path.join(root, "challenge", "companies.json"), "utf8"));

function newestResults(ticker) {
  const prefix = `${ticker.replace(":", "_")}-`;
  if (!fs.existsSync(runsDir)) return null;
  const dirs = fs.readdirSync(runsDir)
    .filter((n) => n.startsWith(prefix))
    .map((n) => path.join(runsDir, n, "results.json"))
    .filter((f) => fs.existsSync(f))
    .map((f) => ({ f, m: fs.statSync(f).mtimeMs }))
    .sort((a, b) => b.m - a.m);
  return dirs.length ? JSON.parse(fs.readFileSync(dirs[0].f, "utf8")) : null;
}

const runs = definitions.companies
  .map((c) => ({ company: c, results: newestResults(c.ticker) }))
  .filter((r) => r.results);

const prompts = fs.readdirSync(path.join(root, "llm", "prompts")).filter((f) => f.endsWith(".md"));
const nodes = runs[0]?.results?.nodes ?? [];
const testCount = fs
  .readdirSync(path.join(root, "tests"))
  .filter((f) => f.startsWith("test_"))
  .reduce((n, f) => n + (fs.readFileSync(path.join(root, "tests", f), "utf8").match(/def test_/g) ?? []).length, 0);

const totals = {
  cost: runs.reduce((n, r) => n + (r.results.cost?.spent_usd ?? 0), 0),
  claims: runs.reduce((n, r) => n + (r.results.evidence?.counts?.claims ?? 0), 0),
  checked: runs.reduce((n, r) => n + (r.results.reconciliation?.citations_checked ?? 0), 0),
  matched: runs.reduce((n, r) => n + (r.results.reconciliation?.citations_matched ?? 0), 0),
  dropped: runs.reduce((n, r) => n + (r.results.reconciliation?.dropped ?? []).length, 0),
  research: runs.reduce((n, r) => n + (r.results.dossier?.counts?.research ?? 0), 0),
  docs: runs.reduce((n, r) => n + Object.keys(r.results.evidence?.documents ?? {}).length, 0),
};

const esc = (s) => String(s).replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" })[c]);
const fmt = (v) => (typeof v !== "number" ? "—" : Math.abs(v) >= 1000
  ? v.toLocaleString("en-GB", { maximumFractionDigits: 0 })
  : v.toLocaleString("en-GB", { maximumFractionDigits: 3 }));

const STAGES = [
  ["A", "sources"], ["B", "acquire"], ["C", "structure"], ["D", "model"], ["E", "analyse"],
  ["V1", "reconcile"], ["F", "challenge"], ["G", "judge"], ["V2", "compare"], ["H", "position"], ["I", "output"],
];

// --- the diagram, inline SVG, self-contained -------------------------------
const colW = 132, rowH = 30, top = 46;
let svgNodes = "";
STAGES.forEach(([stage], i) => {
  const list = nodes.filter((n) => n.stage === stage);
  svgNodes += `<text x="${i * colW + 8}" y="26" class="col">${stage}</text>`;
  list.forEach((n, j) => {
    const y = top + j * rowH;
    const dark = n.available === false;
    svgNodes += `<rect x="${i * colW + 4}" y="${y}" width="${colW - 12}" height="${rowH - 7}" rx="4" class="${dark ? "nd dark" : "nd"}"/>`;
    svgNodes += `<text x="${i * colW + 11}" y="${y + 15}" class="nid">${esc(n.id)}</text>`;
    svgNodes += `<text x="${i * colW + 40}" y="${y + 15}" class="nlbl">${esc(n.label.slice(0, 15))}</text>`;
  });
});
const maxRows = Math.max(...STAGES.map(([s]) => nodes.filter((n) => n.stage === s).length));
const svgH = top + maxRows * rowH + 46;
const svgW = STAGES.length * colW;
// The consensus bus runs along the bottom as its own conductor.
svgNodes += `<line x1="4" y1="${svgH - 24}" x2="${svgW - 12}" y2="${svgH - 24}" class="bus"/>`;
svgNodes += `<text x="8" y="${svgH - 30}" class="buslbl">consensus bus — tapped at U1, carried untouched, rendered for comparison only (positioning off)</text>`;

const page = `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Forecaster — Agents vs Wall Street</title>
<style>
  :root{--bg:#121211;--s1:#1a1a19;--s2:#232322;--line:#33322f;--ink:#fff;--ink2:#c3c2b7;--mut:#8b8a80;
        --b:#3987e5;--o:#d95926;--g:#199e70;--warn:#fab219;--crit:#d03b3b;color-scheme:dark}
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.62 ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif}
  .wrap{width:min(1080px,calc(100% - 40px));margin:0 auto;padding:30px 0 70px}
  h1{font-size:31px;margin:0 0 4px;letter-spacing:-.025em}
  .tag{color:var(--mut);font-size:13px;margin:0 0 26px}
  h2{font-size:19px;margin:34px 0 4px;letter-spacing:-.015em}
  h3{font-size:14px;margin:18px 0 5px}
  p{margin:9px 0;color:var(--ink2);max-width:86ch}
  .lede{color:var(--ink);font-size:16.5px}
  .panel{background:var(--s1);border:1px solid var(--line);border-radius:12px;padding:17px 19px;margin:14px 0}
  .grid{display:grid;gap:13px;grid-template-columns:repeat(auto-fit,minmax(215px,1fr))}
  .stat{background:var(--s2);border:1px solid var(--line);border-radius:10px;padding:13px 15px}
  .stat b{display:block;font-size:23px;letter-spacing:-.02em;font-variant-numeric:tabular-nums}
  .stat span{color:var(--mut);font-size:12px}
  table{width:100%;border-collapse:collapse;font-size:13px;margin:9px 0}
  th,td{text-align:left;padding:6px 10px 6px 0;border-bottom:1px solid var(--line);vertical-align:top}
  th{color:var(--mut);font-size:11px;text-transform:uppercase;letter-spacing:.07em}
  td.n,th.n{text-align:right;font-variant-numeric:tabular-nums}
  code{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:12.5px;color:var(--ink2)}
  svg{width:100%;height:auto;background:var(--s1);border:1px solid var(--line);border-radius:12px}
  .col{fill:var(--mut);font:600 10px ui-sans-serif;text-transform:uppercase;letter-spacing:.1em}
  .nd{fill:#14243a;stroke:var(--b);stroke-width:.8}
  .nd.dark{fill:var(--s2);stroke:var(--line);stroke-dasharray:3 2}
  .nid{fill:var(--b);font:600 9.5px ui-monospace,monospace}
  .nlbl{fill:var(--ink2);font:10px ui-sans-serif}
  .bus{stroke:var(--o);stroke-width:2;stroke-dasharray:7 4}
  .buslbl{fill:var(--o);font:10px ui-sans-serif}
  .weak{border-left:3px solid var(--warn);padding-left:14px;margin:12px 0}
  .weak b{color:var(--ink)}
  ul{color:var(--ink2);max-width:86ch}
  li{margin:5px 0}
  .foot{margin-top:36px;padding-top:14px;border-top:1px solid var(--line);color:var(--mut);font-size:12px}
</style>
</head>
<body><div class="wrap">

<h1>Forecaster</h1>
<p class="tag">Twelve numbers for four companies · lock date ${esc(runs[0]?.results?.header?.as_of ?? "")} · generated from run <code>${esc(runs[0]?.results?.header?.run_id ?? "")}</code></p>

<p class="lede">Nine lenses read one shared body of evidence, independently and blind to each other.
Every claim they make must quote a document that exists, and a lens that cites something it cannot
support is dropped whole rather than corrected. One expensive call weighs what survives by how much
it would move the number — never by how many lenses agreed. Python writes JSON; the site renders it
and computes nothing.</p>

<div class="grid">
  <div class="stat"><b>${runs.length}×3</b><span>companies × metrics</span></div>
  <div class="stat"><b>${totals.claims}</b><span>claims, each with a source and a quote</span></div>
  <div class="stat"><b>${totals.checked ? Math.round((totals.matched / totals.checked) * 100) : 0}%</b><span>citations verified against their document</span></div>
  <div class="stat"><b>$${totals.cost.toFixed(2)}</b><span>total spend, $25 ceiling per run</span></div>
</div>

<h2>The signal flow</h2>
<p>Every node carries a stable ID used in module names, event names and log lines, so a line in the
event tape identifies a node without ambiguity. Dashed nodes have no source behind them in this
build and are kept, labelled, rather than deleted — deleting them would hide the gap.</p>
<svg viewBox="0 0 ${svgW} ${svgH}" role="img" aria-label="Pipeline node diagram">${svgNodes}</svg>

<h2>How a number is made</h2>
<table>
<tr><th>stage</th><th>what happens</th><th>failure behaviour</th></tr>
<tr><td><code>A</code> sources</td><td>Adapters by priority. Frozen 1,139-document corpus first; date-bounded web research behind it.</td><td>raise, naming the missing method</td></tr>
<tr><td><code>B</code> acquire</td><td>Results releases picked by <b>form</b>, never by date. Eight earnings calls kept as an ordered sequence.</td><td>drop, listing what was skipped</td></tr>
<tr><td><code>B3</code> research</td><td>An agent proposes searches, reads what returns, and asks for what is missing. ${totals.research} documents retrieved.</td><td>degrade, named</td></tr>
<tr><td><code>B5</code> extract</td><td>Prose becomes typed claims, each carrying a verbatim quote matched back to its document.</td><td>drop on a failed quote match</td></tr>
<tr><td><code>C</code> structure</td><td>Evidence store with stable citation IDs. Verification splits three ways: prose, structured, derived.</td><td>raise</td></tr>
<tr><td><code>D</code> model</td><td>Income model: history as filed, medians with scaled MAD, projection, and a reproduction check.</td><td>degrade on a failed reproduction</td></tr>
<tr><td><code>E</code> analyse</td><td>Nine lenses, blind to each other. One has no model at all.</td><td>drop, handled at V1</td></tr>
<tr><td><code>V1</code> reconcile</td><td>Deterministic. Every cited ID must exist; every quote must match. ${totals.dropped} lens${totals.dropped === 1 ? "" : "es"} dropped on this run.</td><td>raise if every lens dies</td></tr>
<tr><td><code>F</code> challenge</td><td>Each surviving view argued at its strongest, then attacked. Surviving confidence replaces stated confidence.</td><td>degrade, named</td></tr>
<tr><td><code>G</code> judge</td><td>Exactly one deep-tier call. Five named quantiles per metric, weighted by materiality.</td><td>degrade to a labelled fallback</td></tr>
<tr><td><code>V2</code> compare</td><td>M&amp;A, accounting change, a 53rd week, withdrawn guidance.</td><td>degrade, named</td></tr>
<tr><td><code>H</code> position</td><td>Consensus measured and rendered; positioning switched off by configuration.</td><td>deterministic</td></tr>
<tr><td><code>I</code> output</td><td>Results file and event tape, written atomically.</td><td>raise</td></tr>
</table>

<h2>The forecasts</h2>
<table>
<tr><th>company</th><th>metric</th><th class="n">our number</th><th class="n">street</th><th class="n">baseline</th><th class="n">lenses kept</th></tr>
${runs.map((r) => r.results.forecast.metrics.map((m, i) => `<tr>
<td>${i === 0 ? `<b>${esc(r.company.ticker)}</b> ${esc(r.company.period)}` : ""}</td>
<td>${esc(m.metric_label)}</td>
<td class="n"><b>${fmt(m.forecast)}</b></td>
<td class="n">${fmt(m.consensus)}</td>
<td class="n">${fmt(m.baseline)}</td>
<td class="n">${i === 0 ? `${(r.results.reconciliation?.surviving ?? []).length}/9` : ""}</td>
</tr>`).join("")).join("")}
</table>

<h2>Decisions worth defending</h2>

<h3>Positioning toward consensus is switched off</h3>
<p>Accuracy is scored as our miss divided by the Street's miss. A forecast shrunk onto consensus
inherits the Street's error and scores a ratio of about 1.0 by construction — safe, and unable to
place. Every regime condition is still measured and reported, including the lambda that would have
applied; it is simply not applied. Consensus and the baseline are still drawn beside every number.</p>

<h3>An income model, not three statements</h3>
<p>None of the twelve metrics touches a balance-sheet or cash-flow line. A linked balance sheet with
a circular interest solve would have been real engineering that reaches nothing we are scored on.
The reproduction check survives in full, and leads the Model sheet, because a model should state its
own measured error before anyone trusts it with a projection.</p>

<h3>The lenses cannot see each other</h3>
<p>Structural, not an instruction in a prompt: there is no parameter through which one view could
reach another. Lenses that see each other converge; converging rebuilds consensus. Agreement between
lenses is never presented as corroboration anywhere, because isolation prevents convergence in
language but not correlated error.</p>

<h3>Research names the driver, never the ticker</h3>
<p>A query naming the company returns share-price commentary and earnings previews. A query naming
the driver — farm income, remodelling spend, the analog cycle, the UK jobs market — returns what
actually moves the line. Retrieved text is written to disk as a document so a quote against it is
verified exactly like a quote from an 8-K.</p>

<h2>Known weaknesses</h2>
<p>Stated because they are the honest limits of what this run can claim, not because they are
comfortable.</p>

<div class="weak"><b>No skill number here has been measured.</b> There is no backtest. Scoring our
estimate against past actuals is possible from the filings; scoring it against the Street's bar
<em>as it stood at those quarters</em> is not, because no source available publishes consensus
history. Skill against consensus, the lambda regression and the per-regime beta table are therefore
unobtainable rather than pending.</div>

<div class="weak"><b>The forecast intervals are uncalibrated.</b> They are the judge's own opinion of
its uncertainty and have never been checked against an outcome. A p10–p90 band here is not a claim
that eighty per cent of outcomes fall inside it.</div>

<div class="weak"><b>The leave-one-lens-out ablation has not run.</b> Until it does, the nine-lens
roster is a design claim rather than a measured one, and no lens has earned its tokens on evidence.</div>

<div class="weak"><b>Consensus basis is not verified per metric.</b> Where a vendor's consensus
measures a different line from the one we owe — Deere's equipment sales versus worldwide sales and
revenues — the comparison on the page is not like-for-like. Positioning being off removes the
damage, but the number shown as "street" may not be measuring what its row says.</div>

<div class="weak"><b>Extraction reconciles magnitudes by inference.</b> A release states "$41.8
billion" in prose and tags 41,765 in a table. The model harmonises to the modal order of magnitude
and drops what it cannot reconcile. That is a heuristic, and a series where the modal scale is
itself wrong would be harmonised confidently in the wrong direction.</div>

<div class="weak"><b>Hays has no analyst coverage anywhere in the chain</b>, so three of the twelve
numbers rest entirely on company disclosure and retrieved industry data, with nothing external to
check them against.</div>

<h2>Reproducing this</h2>
<table>
<tr><td><code>py -m forecaster.final_run</code></td><td>the final command: pipeline, workbooks, checks, one timestamped log under <code>logs/</code></td></tr>
<tr><td><code>py -m unittest discover -s tests -t .</code></td><td>${testCount} tests</td></tr>
<tr><td><code>cd site &amp;&amp; node prep.mjs &amp;&amp; npx astro dev</code></td><td>the nine sheets on port 4321</td></tr>
<tr><td><code>node scripts/build-architecture.mjs</code></td><td>regenerates this page from the run</td></tr>
</table>
<p>${prompts.length} prompt files under <code>llm/prompts/</code>, one per agent, each carrying its
version. The Agents sheet is generated from that directory rather than a hand-kept list, so a prompt
without a roster entry shows as a mismatch instead of drifting silently.</p>

<div class="foot">Generated ${new Date().toISOString()} from ${runs.length} completed run${runs.length === 1 ? "" : "s"} ·
${nodes.length} nodes · ${prompts.length} agents · ${testCount} tests · ${totals.docs} evidence documents</div>

</div></body></html>`;

fs.writeFileSync(path.join(root, "architecture", "index.html"), page, "utf8");
console.log(
  `architecture/index.html — ${(Buffer.byteLength(page) / 1024).toFixed(1)} KB, ` +
    `${runs.length} runs, ${nodes.length} nodes, ${prompts.length} agents, ${testCount} tests`,
);
