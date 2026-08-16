// Mission control for Agents vs Wall Street.
// Serves one screen that shows every task the event asks for, with live status
// read straight from the repository: the workbooks, entry.json, the architecture
// page, the logs and git. Manual-only items (uploads, judging, social) are ticked
// by hand and persisted in dashboard/state.json.

import fs from "node:fs/promises";
import fsSync from "node:fs";
import http from "node:http";
import path from "node:path";
import { execFileSync } from "node:child_process";
import XLSX from "xlsx";

const root = path.resolve(import.meta.dirname, "..");
const publicDir = path.join(import.meta.dirname, "public");
const statePath = path.join(import.meta.dirname, "state.json");
const port = Number(process.env.PORT ?? 4173);

// ---------------------------------------------------------------- manual state

async function readManualState() {
  try {
    return JSON.parse(await fs.readFile(statePath, "utf8"));
  } catch {
    return { checked: {}, updatedAt: null };
  }
}

async function writeManualState(state) {
  await fs.writeFile(statePath, `${JSON.stringify(state, null, 2)}\n`, "utf8");
}

// -------------------------------------------------------------------- the day

// Sunday 16 August 2026, London (BST, +01:00).
const DAY = "2026-08-16";
const timeline = [
  { at: "10:30", label: "Briefing", detail: "Welcome, rules, tools and questions." },
  { at: "11:15", label: "Building starts", detail: "Nothing competition-specific may exist before this.", hard: true },
  { at: "13:00", label: "Lunch", detail: "Teams can keep working." },
  { at: "16:00", label: "Judging pass opens", detail: "Five minutes with one judge pair — the heaviest part of architecture judging." },
  { at: "17:00", label: "Social competition closes", detail: "X and LinkedIn posts must be up.", hard: true },
  { at: "17:15", label: "Architecture HTML locks", detail: "45-minute final-run window opens.", hard: true },
  { at: "17:30", label: "OpenStocks uploads open", detail: "Workbook uploads and the private entry form.", hard: true },
  { at: "18:00", label: "Hard deadline", detail: "Four workbooks uploaded and the private entry recorded.", hard: true },
].map((entry) => ({ ...entry, iso: `${DAY}T${entry.at}:00+01:00` }));

// ------------------------------------------------------------ automatic checks

const PLACEHOLDERS = [
  "Team name",
  "add agent name",
  "add everyone's name",
  "add headless agent, coding harness or hybrid setup",
  "add the primary models",
  "add commit hash",
  "add command",
  "In one sentence, explain what your agent does",
  "Explain the path from a company ticker",
  "Explain how the agent finds information",
  "Describe the decisions that most affected",
  "Show how the system catches missing information",
  "State what someone needs to install",
  "Be specific about where the agent still needs human help",
];

function item(id, label, extra = {}) {
  return { id, label, kind: "auto", status: "todo", detail: "", ...extra };
}

async function readJson(file) {
  try {
    return JSON.parse(await fs.readFile(path.join(root, file), "utf8"));
  } catch {
    return null;
  }
}

function isFilled(value) {
  return typeof value === "string" && value.trim() !== "" && value.trim().toUpperCase() !== "TBC";
}

function git(args) {
  try {
    return execFileSync("git", args, { cwd: root, encoding: "utf8" }).trim();
  } catch {
    return null;
  }
}

async function forecastSection() {
  const definitions = await readJson("challenge/companies.json");
  const items = [];
  const companies = [];

  for (const company of definitions?.companies ?? []) {
    const filePath = path.join(root, "submission", company.outputFile);
    let sheet = null;
    let fileError = null;

    if (!fsSync.existsSync(filePath)) {
      fileError = "workbook not created yet";
    } else {
      try {
        const workbook = XLSX.readFile(filePath, { cellFormula: false });
        sheet = workbook.Sheets.Summary;
        if (!sheet) fileError = "Summary sheet is missing";
      } catch (error) {
        fileError = `cannot be opened as .xlsx (${error.message})`;
      }
    }

    const cell = (row, column) =>
      sheet?.[XLSX.utils.encode_cell({ r: row - 1, c: column - 1 })]?.v;
    const text = (value) => String(value ?? "").trim();

    let headerRow = null;
    if (sheet) {
      for (let row = 1; row <= 30; row += 1) {
        if (
          text(cell(row, 1)) === "Metric" &&
          text(cell(row, 2)) === "Units" &&
          text(cell(row, 3)) === company.period
        ) {
          headerRow = row;
          break;
        }
      }
      if (!headerRow) fileError = `Metric / Units / ${company.period} header not found`;
    }

    const companyItems = company.metrics.map((metric, index) => {
      const id = `forecast:${company.ticker}:${index}`;
      const label = `${company.ticker} — ${metric.label}`;
      if (fileError) {
        return item(id, label, { status: "todo", detail: fileError, units: metric.units });
      }
      const row = headerRow + index + 1;
      if (text(cell(row, 1)) !== metric.label) {
        return item(id, label, { status: "warn", detail: `row label must read “${metric.label}”`, units: metric.units });
      }
      if (text(cell(row, 2)) !== metric.units) {
        return item(id, label, { status: "warn", detail: `units must read “${metric.units}”`, units: metric.units });
      }
      const value = cell(row, 3);
      if (typeof value !== "number" || !Number.isFinite(value)) {
        return item(id, label, { status: "todo", detail: "no numeric forecast yet — a blank scores 5.0", units: metric.units });
      }
      return item(id, label, {
        status: "done",
        detail: `${value} ${metric.units}`,
        units: metric.units,
        value,
      });
    });

    companies.push({
      ticker: company.ticker,
      name: company.company,
      period: company.period,
      file: company.outputFile,
      error: fileError,
      metrics: companyItems.map((entry) => ({
        label: entry.label.split(" — ")[1],
        units: entry.units,
        value: entry.value ?? null,
        status: entry.status,
        detail: entry.detail,
      })),
      done: companyItems.filter((entry) => entry.status === "done").length,
      total: companyItems.length,
    });
    items.push(...companyItems);
  }

  return { items, companies };
}

async function entrySection() {
  const entry = await readJson("entry.json");
  const items = [];

  if (!entry) {
    return [item("entry:file", "entry.json exists", { detail: "run npm run setup:entry" })];
  }

  const check = (id, label, ok, detail) =>
    items.push(item(id, label, { status: ok ? "done" : "todo", detail }));

  check("entry:agentName", "Agent name", isFilled(entry.agentName), entry.agentName);
  check("entry:description", "One-line description", isFilled(entry.oneLineDescription), entry.oneLineDescription);

  const members = Array.isArray(entry.teamMembers) ? entry.teamMembers : [];
  const membersOk =
    members.length >= 1 &&
    members.length <= 4 &&
    members.every((member) => isFilled(member?.name) && /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(String(member?.email ?? "")));
  check(
    "entry:team",
    "Every team member's name and email",
    membersOk,
    members.map((member) => member?.name).filter(Boolean).join(", ") || "none listed",
  );

  const setup = entry.technicalSetup ?? {};
  check("entry:harness", "Harness or framework", isFilled(setup.harnessOrFramework), setup.harnessOrFramework);
  check(
    "entry:models",
    "Primary models",
    Array.isArray(setup.primaryModels) && setup.primaryModels.length > 0,
    (setup.primaryModels ?? []).join(", ") || "list is empty",
  );
  check(
    "entry:languages",
    "Languages and frameworks",
    Array.isArray(setup.languagesAndFrameworks) && setup.languagesAndFrameworks.length > 0,
    (setup.languagesAndFrameworks ?? []).join(", ") || "list is empty",
  );
  check(
    "entry:preexisting",
    "Pre-existing components declared",
    Array.isArray(setup.preExistingComponents) && setup.preExistingComponents.length > 0,
    `${(setup.preExistingComponents ?? []).length} declared — undeclared reuse is a disqualification risk`,
  );
  check(
    "entry:humanInput",
    "Human input during the final run",
    isFilled(setup.humanInputDuringFinalRun),
    setup.humanInputDuringFinalRun,
  );

  const submission = entry.submission ?? {};
  let repoOk = false;
  try {
    const url = new URL(submission.repositoryUrl);
    repoOk =
      url.protocol === "https:" &&
      url.hostname.toLowerCase().replace(/^www\./, "") === "github.com" &&
      url.pathname.split("/").filter(Boolean).length === 2;
  } catch {
    repoOk = false;
  }
  check("entry:repo", "Repository URL", repoOk, submission.repositoryUrl ?? "not set");

  const head = git(["rev-parse", "HEAD"]);
  const commit = String(submission.finalCommit ?? "").trim();
  const commitOk = /^[a-f0-9]{7,40}$/i.test(commit);
  const matchesHead = commitOk && head && head.startsWith(commit.toLowerCase());
  items.push(
    item("entry:commit", "Final commit hash", {
      status: commitOk ? (matchesHead ? "done" : "warn") : "todo",
      detail: commitOk
        ? matchesHead
          ? `${commit} — matches HEAD`
          : `${commit} — does not match HEAD ${head?.slice(0, 12) ?? "unknown"}`
        : "not recorded",
    }),
  );
  check("entry:command", "Final command", isFilled(submission.finalCommand), submission.finalCommand ?? "not set");
  check("entry:emailUse", "Email use confirmed by everyone", entry.emailUseConfirmed === true, "");

  return items;
}

async function architectureSection() {
  const filePath = path.join(root, "architecture", "index.html");
  let html = "";
  let bytes = 0;
  try {
    const buffer = await fs.readFile(filePath);
    bytes = buffer.byteLength;
    html = buffer.toString("utf8");
  } catch {
    return [item("arch:file", "architecture/index.html exists", { detail: "file is missing" })];
  }

  const remaining = PLACEHOLDERS.filter((phrase) => html.toLowerCase().includes(phrase.toLowerCase()));
  const external = /(?:src|href)\s*=\s*["']https?:\/\//i.test(html);
  const hasDiagram = /<svg|<img|```|<pre|mermaid/i.test(html);
  const weaknesses = /known weakness/i.test(html) && !remaining.some((p) => p.startsWith("Be specific"));

  return [
    item("arch:size", "Self-contained and under 2 MB", {
      status: bytes > 0 && bytes <= 2 * 1024 * 1024 ? "done" : "todo",
      detail: `${(bytes / 1024).toFixed(1)} KB`,
    }),
    item("arch:placeholders", "Template placeholder text replaced", {
      status: remaining.length === 0 ? "done" : "todo",
      detail: remaining.length ? `${remaining.length} left: “${remaining[0]}”` : "none left",
    }),
    item("arch:offline", "No external assets or network requests", {
      status: external ? "warn" : "done",
      detail: external ? "found an http(s) src/href — it will not load in the judging preview" : "nothing loads from the network",
    }),
    item("arch:diagram", "Diagram or worked trace included", {
      status: hasDiagram ? "done" : "todo",
      detail: hasDiagram ? "found inline figure or code block" : "10 points ride on a diagram that matches the real system",
    }),
    item("arch:weaknesses", "Known weaknesses written honestly", {
      status: weaknesses ? "done" : "todo",
      detail: "6 points for what you tried, changed, abandoned and where it fails",
    }),
  ];
}

async function runSection() {
  const items = [];
  let logs = [];
  try {
    logs = (await fs.readdir(path.join(root, "logs"))).filter((name) => name !== "README.md");
  } catch {
    logs = [];
  }
  items.push(
    item("run:log", "Timestamped clear-run log saved in logs/", {
      status: logs.length ? "done" : "todo",
      detail: logs.length ? logs.join(", ") : "no log files yet",
    }),
  );

  const dirty = git(["status", "--porcelain"]);
  const head = git(["rev-parse", "--short", "HEAD"]);
  items.push(
    item("run:committed", "Final-run version committed", {
      status: dirty === "" ? "done" : "warn",
      detail: dirty === ""
        ? `working tree clean at ${head}`
        : `${dirty.split("\n").filter(Boolean).length} uncommitted change(s) — history is the anti-pre-build evidence`,
    }),
  );

  return items;
}

// ---------------------------------------------------------------- manual items

const manualSections = [
  {
    id: "uploads",
    title: "Manual uploads",
    subtitle: "By rule the agent must not post to OpenStocks. From 17:30, a human uploads each workbook.",
    items: [
      { id: "upload:HD", label: "HD-FY2026Q2.xlsx uploaded to Home Depot" },
      { id: "upload:ADI", label: "ADI-FY2026Q3.xlsx uploaded to Analog Devices" },
      { id: "upload:HAS", label: "HAS-FY2026.xlsx uploaded to Hays plc" },
      { id: "upload:DE", label: "DE-FY2026Q3.xlsx uploaded to Deere & Company" },
      { id: "upload:form", label: "Private entry form submitted", hint: "entry.json + architecture/index.html at openstocks.com/hackathon" },
      { id: "upload:signin", label: "Signed in to OpenStocks and can reach all four Forecast Models" },
    ],
  },
  {
    id: "judging",
    title: "Judging — the 100-point overlay",
    subtitle: "Tick a category once the system visibly demonstrates it. The bar is points covered, not tasks done.",
    weighted: true,
    items: [
      { id: "judge:approach", label: "Forecasting approach", weight: 16, hint: "Reasoning trail, not a model asked for a number." },
      { id: "judge:model", label: "Model quality", weight: 12, hint: "Evidence and assumptions traceable to each of the 12 numbers." },
      { id: "judge:data", label: "Data approach", weight: 12, hint: "Sources, citations, retrieval code, freshness checks." },
      { id: "judge:validation", label: "Validation and reliability", weight: 12, hint: "Unit checks, outlier checks, conflicts, rejected values, run log." },
      { id: "judge:harness", label: "Agent harness", weight: 9, hint: "Repo structure, final command, orchestration, evidence it ran." },
      { id: "judge:tooling", label: "Tooling and ergonomics", weight: 9, hint: "Search, extraction and checking tools that improved the run." },
      { id: "judge:clarity", label: "Write-up clarity", weight: 10, hint: "An outsider understands it in five minutes." },
      { id: "judge:diagram", label: "Diagram and accuracy", weight: 10, hint: "Diagram matches the code; repo instructions reproduce the run." },
      { id: "judge:honesty", label: "Honesty and self-knowledge", weight: 6, hint: "Trade-offs, failed approaches, limitations." },
      { id: "judge:craft", label: "Craft", weight: 4, hint: "Publishable on the OpenStocks profile." },
      { id: "judge:conversation", label: "Five-minute judge conversation done", weight: 0, hint: "16:00–17:15 — the most heavily weighted piece of judging." },
    ],
  },
  {
    id: "social",
    title: "Social competition",
    subtitle: "Two individual $500 prizes. Closes at 17:00 — before the build deadline, so post early.",
    items: [
      { id: "social:x", label: "X post published" },
      { id: "social:linkedin", label: "LinkedIn post published" },
    ],
  },
  {
    id: "rules",
    title: "Rules and fair play",
    subtitle: "The disqualification rule has no partial credit, so treat these as gates.",
    items: [
      { id: "rules:read", label: "Team has read and accepts the hackathon and prize rules" },
      { id: "rules:builtToday", label: "All competition-specific work created after 11:15 today", hint: "Prompts, retrieval, forecasting logic, this dashboard, the architecture page." },
      { id: "rules:noHandForecasts", label: "No hand-made forecasts — every number came from the described system" },
      { id: "rules:noPublicEntry", label: "entry.json kept out of any public repository" },
    ],
  },
];

// ------------------------------------------------------------------ assembling

function summarise(items) {
  const total = items.length;
  const done = items.filter((entry) => entry.status === "done").length;
  const warn = items.filter((entry) => entry.status === "warn").length;
  return { done, warn, total, pct: total ? Math.round((done / total) * 100) : 0 };
}

async function buildState() {
  const manual = await readManualState();
  const forecasts = await forecastSection();

  const sections = [
    {
      id: "forecasts",
      title: "The 12 forecasts",
      subtitle: "Read live from submission/*.xlsx. A missing number scores 5.0 — the worst outcome on the board.",
      items: forecasts.items,
      companies: forecasts.companies,
    },
    {
      id: "entry",
      title: "Entry record",
      subtitle: "entry.json — everything npm run check:entry demands.",
      items: await entrySection(),
    },
    {
      id: "architecture",
      title: "Architecture page",
      subtitle: "architecture/index.html — locks at 17:15 and carries 30 of the 100 judging points.",
      items: await architectureSection(),
    },
    {
      id: "run",
      title: "Clear run",
      subtitle: "One execution from the declared commit that produces all four workbooks.",
      items: await runSection(),
    },
    ...manualSections.map((section) => ({
      ...section,
      items: section.items.map((entry) => ({
        ...entry,
        kind: "manual",
        detail: entry.hint ?? "",
        status: manual.checked?.[entry.id] ? "done" : "todo",
      })),
    })),
  ];

  for (const section of sections) {
    section.progress = summarise(section.items);
    if (section.weighted) {
      const totalPoints = section.items.reduce((sum, entry) => sum + (entry.weight ?? 0), 0);
      const donePoints = section.items
        .filter((entry) => entry.status === "done")
        .reduce((sum, entry) => sum + (entry.weight ?? 0), 0);
      section.points = { done: donePoints, total: totalPoints };
      section.progress.pct = totalPoints ? Math.round((donePoints / totalPoints) * 100) : 0;
    }
  }

  const allItems = sections.flatMap((section) => section.items);

  return {
    now: new Date().toISOString(),
    day: DAY,
    timeline,
    sections,
    overall: summarise(allItems),
    git: { head: git(["rev-parse", "--short", "HEAD"]), branch: git(["rev-parse", "--abbrev-ref", "HEAD"]) },
  };
}

// ---------------------------------------------------------------------- server

const mimeTypes = { ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8" };

function send(response, status, body, type = "application/json; charset=utf-8") {
  response.writeHead(status, { "content-type": type, "cache-control": "no-store" });
  response.end(body);
}

const server = http.createServer(async (request, response) => {
  const url = new URL(request.url, `http://${request.headers.host}`);

  try {
    if (request.method === "GET" && url.pathname === "/api/state") {
      return send(response, 200, JSON.stringify(await buildState()));
    }

    if (request.method === "POST" && url.pathname === "/api/toggle") {
      const chunks = [];
      for await (const chunk of request) chunks.push(chunk);
      const { id, done } = JSON.parse(Buffer.concat(chunks).toString("utf8"));
      const known = manualSections.some((section) => section.items.some((entry) => entry.id === id));
      if (!known) return send(response, 400, JSON.stringify({ error: "unknown task id" }));
      const state = await readManualState();
      state.checked = state.checked ?? {};
      if (done) state.checked[id] = true;
      else delete state.checked[id];
      state.updatedAt = new Date().toISOString();
      await writeManualState(state);
      return send(response, 200, JSON.stringify(await buildState()));
    }

    const name = url.pathname === "/" ? "index.html" : path.basename(url.pathname);
    const filePath = path.join(publicDir, name);
    if (!filePath.startsWith(publicDir)) return send(response, 403, "forbidden", "text/plain");
    const body = await fs.readFile(filePath);
    return send(response, 200, body, mimeTypes[path.extname(filePath)] ?? "application/octet-stream");
  } catch (error) {
    if (error.code === "ENOENT") return send(response, 404, "not found", "text/plain");
    return send(response, 500, JSON.stringify({ error: error.message }));
  }
});

server.listen(port, () => {
  console.log(`Mission control on http://localhost:${port}`);
});
