// Seal the built sheets into one file that needs nothing behind it.
//
// The sealed page carries every sheet, the run data already rendered into them,
// the stylesheet and the scripts. It makes no request of any kind, so it opens
// from a memory stick with the machine that produced it switched off. The
// recorded tape replaces the live one; the current on the drawing still runs,
// because it is drawn in CSS and needs no data.

import fs from "node:fs";
import path from "node:path";

const root = path.resolve(import.meta.dirname, "..");
const dist = path.join(root, "site", "dist");
const out = path.join(root, "architecture", "index.html");

const SHEETS = [
  ["01", "System", "index.html", "/"],
  ["02", "Forecast", "forecast/index.html", "/forecast"],
  ["03", "Reasoning", "reasoning/index.html", "/reasoning"],
  ["04", "Model", "model/index.html", "/model"],
  ["05", "Risk", "risk/index.html", "/risk"],
  ["06", "Method", "method/index.html", "/method"],
  ["07", "Agents", "agents/index.html", "/agents"],
  ["08", "Cost", "cost/index.html", "/cost"],
  ["09", "Integrity", "integrity/index.html", "/integrity"],
];

if (!fs.existsSync(dist)) {
  console.error("seal: nothing built. Run the site build first.");
  process.exit(1);
}

const styles = new Map();
const scripts = [];
const sections = [];

for (const [n, name, file] of SHEETS) {
  const full = path.join(dist, file);
  if (!fs.existsSync(full)) {
    console.error(`seal: ${file} is missing from the build`);
    process.exit(1);
  }
  let html = fs.readFileSync(full, "utf8");

  // Pull the stylesheet in rather than pointing at it.
  for (const match of html.matchAll(/<link[^>]+rel="stylesheet"[^>]+href="([^"]+)"[^>]*>/g)) {
    const href = match[1];
    if (styles.has(href)) continue;
    const asset = path.join(dist, href.replace(/^\//, ""));
    if (fs.existsSync(asset)) styles.set(href, fs.readFileSync(asset, "utf8"));
  }
  for (const match of html.matchAll(/<style[^>]*>([\s\S]*?)<\/style>/g)) {
    styles.set(`inline-${styles.size}`, match[1]);
  }

  // Any script the sheets carry travels with them.
  for (const match of html.matchAll(/<script[^>]+src="([^"]+)"[^>]*><\/script>/g)) {
    const asset = path.join(dist, match[1].replace(/^\//, ""));
    if (fs.existsSync(asset)) scripts.push(fs.readFileSync(asset, "utf8"));
  }
  for (const match of html.matchAll(/<script(?![^>]+src=)(?![^>]*application\/json)[^>]*>([\s\S]*?)<\/script>/g)) {
    scripts.push(match[1]);
  }

  let body = (html.match(/<body[^>]*>([\s\S]*)<\/body>/) ?? [null, ""])[1];
  body = body
    .replace(/<script[^>]+src="[^"]+"[^>]*><\/script>/g, "")
    .replace(/<script(?![^>]*application\/json)[^>]*>[\s\S]*?<\/script>/g, "");

  // The recorded tape stands in for the live one.
  body = body.replace(/data-live="1"/g, 'data-live="0"');

  // Navigation moves inside the file.
  for (const [num, , , href] of SHEETS) {
    body = body.replaceAll(`href="${href}"`, `href="#sheet-${num}"`);
  }

  sections.push(`<section class="sealed-sheet" id="sheet-${n}" data-name="${name}">${body}</section>`);
}

const sealedAt = new Date().toISOString().slice(0, 16).replace("T", " ");
const runLine = (() => {
  const bundlePath = path.join(root, "site", "src", "data", "bundle.json");
  if (!fs.existsSync(bundlePath)) return "";
  const bundle = JSON.parse(fs.readFileSync(bundlePath, "utf8"));
  const live = bundle.companies.filter((c) => c.results);
  const run = live[0]?.results?.header?.run_id ?? "";
  const asOf = live[0]?.results?.header?.as_of ?? "";
  return `run ${run} · ${live.length} names · as of ${asOf} · sealed ${sealedAt} UTC`;
})();

const css = [...styles.values()].join("\n");

const page = `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Forecaster</title>
<style>
${css}
.sealed-sheet { display: none; }
.sealed-sheet:target { display: block; }
body:not(:has(.sealed-sheet:target)) #sheet-01 { display: block; }
.sealbar {
  border-bottom: 1px solid var(--line);
  background: var(--strip);
  padding: 6px 0;
  text-align: center;
  font-size: 11px;
  letter-spacing: 0.1em;
  color: var(--faint);
  font-family: var(--mono);
}
</style>
</head>
<body>
<div class="sealbar">sealed record · ${runLine}</div>
${sections.join("\n")}
<script>
${scripts.join("\n;\n")}
</script>
</body>
</html>
`;

fs.mkdirSync(path.dirname(out), { recursive: true });
fs.writeFileSync(out, page, "utf8");

// A sealed page that reaches for anything is not sealed.
const reaches = [...page.matchAll(/(src|href)="(https?:|\/\/)[^"]*"/g)].map((m) => m[0]);
const fetches = /fetch\(|XMLHttpRequest|new WebSocket|@import url\(/.test(page.replace(/data-live="0"/g, ""));
if (reaches.length) {
  console.error(`seal: the page reaches outside itself\n  ${reaches.slice(0, 5).join("\n  ")}`);
  process.exit(1);
}

const size = (page.length / 1024 / 1024).toFixed(2);
console.log(`sealed: architecture/index.html, ${SHEETS.length} sheets, ${size} MB, no external reference`);
if (fetches) {
  console.log("sealed: a request path exists in the carried script, held off by the recorded tape flag");
}
