// Fail the build on a connector dash.
//
// The house rule is that no dash joins two parts of a sentence: not an em dash,
// not an en dash, not a hyphen with a space beside it. Rules that rely on
// discipline do not survive a deadline, so this reads every rendered string and
// every string in the data the sheets render, and names what it finds.

import fs from "node:fs";
import path from "node:path";

const root = path.resolve(import.meta.dirname, "..");
// A string the run wrote is a defect in whatever wrote it, not something the
// sheets repair. Passing --data-warn reports those without stopping the build,
// so a clean interface can ship while a re-run clears the data.
const dataWarn = process.argv.includes("--data-warn");
const offences = [];
const dataOffences = [];

const EM = /—/;
const EN = /–/;
const SPACED = /(^|\S) - (\S|$)/;

// The house glyph for a figure that does not exist is a dash standing on its
// own. A dash that runs straight into a word is joining two parts of a sentence,
// which is the thing being caught. Absence is removed before the test so the two
// uses cannot be confused.
const ABSENCE = /—(?![ \t]*[A-Za-z])/g;

function judge(text, where, into = offences) {
  if (typeof text !== "string" || !text) return;
  const trimmed = text.trim();
  if (trimmed === "—") return;
  const body = text.replace(ABSENCE, " ");
  if (EM.test(body)) into.push({ where, kind: "em dash", text: trimmed.slice(0, 120) });
  else if (EN.test(body)) into.push({ where, kind: "en dash", text: trimmed.slice(0, 120) });
  else if (SPACED.test(body)) into.push({ where, kind: "spaced hyphen", text: trimmed.slice(0, 120) });
}

// Prose, rather than an expression that happens to contain a minus sign.
const PROSE = /[A-Za-z]{2,}\s+[A-Za-z]{2,}/;

// 1. Every string field in the data the sheets read.
function walk(value, where) {
  if (typeof value === "string") return judge(value, where, dataOffences);
  if (Array.isArray(value)) return value.forEach((item, i) => walk(item, `${where}[${i}]`));
  if (value && typeof value === "object") {
    for (const [key, item] of Object.entries(value)) walk(item, `${where}.${key}`);
  }
}

const bundlePath = path.join(root, "site", "src", "data", "bundle.json");
if (fs.existsSync(bundlePath)) {
  walk(JSON.parse(fs.readFileSync(bundlePath, "utf8")), "bundle");
}

// 2. Every string the interface itself writes.
const sources = [];
const collect = (dir) => {
  if (!fs.existsSync(dir)) return;
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) collect(full);
    else if (/\.(astro|mjs|css)$/.test(entry.name)) sources.push(full);
  }
};
collect(path.join(root, "site", "src"));

// What counts as a written string is the text the interface puts on a page:
// quoted text in code, and the content between tags in markup. Arithmetic is
// not prose, and a minus sign in an expression is not a connector dash.
for (const file of sources) {
  const text = fs.readFileSync(file, "utf8");
  const isAstro = file.endsWith(".astro");
  const lines = text.split("\n");
  let fences = 0;

  lines.forEach((line, i) => {
    const where = `${path.relative(root, file)}:${i + 1}`;
    if (isAstro && line.trim() === "---") {
      fences += 1;
      return;
    }
    // Comments explain the build to a reader of the code, not to a reader of the
    // interface, so they sit outside the rule.
    if (/^\s*(\/\/|\*|\/\*)/.test(line)) return;

    // In markup, the text between the tags with every expression removed.
    const inMarkup = isAstro && fences >= 2 && !/^\s*(import|const|let|export)\b/.test(line);
    if (inMarkup) {
      const content = line
        .replace(/\{[^}]*\}/g, " ")
        .replace(/<[^>]*>/g, " ")
        .trim();
      if (PROSE.test(content)) judge(content, where);
      return;
    }

    // In code, only quoted text, and only where it reads as a sentence.
    for (const match of line.matchAll(/"([^"]{6,})"|'([^']{6,})'/g)) {
      const piece = match[1] ?? match[2];
      if (!PROSE.test(piece)) continue;
      // A path, a selector or a class list is a name rather than a sentence.
      if (/^[\w./#-]+$/.test(piece.trim())) continue;
      judge(piece, where);
    }
  });
}

// 3. The rendered pages, which is the only place a joined string can hide after
// two clean inputs.
const dist = path.join(root, "site", "dist");
const pages = [];
const collectPages = (dir) => {
  if (!fs.existsSync(dir)) return;
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) collectPages(full);
    else if (entry.name.endsWith(".html")) pages.push(full);
  }
};
collectPages(dist);

// A rendered line that reproduces a string the run wrote is that writer's
// defect showing through, not a sentence the interface composed. Attributing it
// to the right place is the difference between a fix and a workaround.
const carriedText = dataOffences.map((o) => o.text.slice(0, 40)).filter((t) => t.length > 20);

for (const page of pages) {
  const html = fs.readFileSync(page, "utf8");
  const text = html
    .replace(/<script[\s\S]*?<\/script>/g, " ")
    .replace(/<style[\s\S]*?<\/style>/g, " ")
    .replace(/<[^>]+>/g, "\n");
  text.split("\n").forEach((line) => {
    const where = path.relative(root, page);
    const plain = line.replace(/&#39;/g, "'").replace(/&amp;/g, "&").replace(/&quot;/g, '"');
    const carried = carriedText.some((t) => plain.includes(t));
    judge(line, where, carried ? dataOffences : offences);
  });
}

const dedupe = (list) => {
  const seen = new Set();
  return list.filter((o) => {
    const key = `${o.kind}|${o.text}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
};

const written = dedupe(offences);
const carried = dedupe(dataOffences);

if (written.length) {
  console.error(`punctuation: ${written.length} string(s) written by the interface carry a connector dash\n`);
  for (const o of written.slice(0, 40)) console.error(`  ${o.kind}  ${o.where}\n    ${o.text}\n`);
}

if (carried.length) {
  const where = [...new Set(carried.map((o) => o.where.replace(/\[\d+\]/g, "[]")))];
  console.error(
    `punctuation: ${carried.length} string(s) arriving from the run carry a connector dash, ` +
      `across ${where.length} field(s). The defect belongs to whatever wrote the string.\n`,
  );
  for (const o of carried.slice(0, 12)) console.error(`  ${o.kind}  ${o.where}\n    ${o.text}\n`);
}

if (written.length || (carried.length && !dataWarn)) process.exit(1);

console.log(
  `punctuation: interface clean across ${sources.length} source files and ${pages.length} pages` +
    (carried.length ? `, ${carried.length} carried string(s) reported` : ""),
);
