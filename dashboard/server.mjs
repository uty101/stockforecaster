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
// Only the deadlines that cost something if missed. The countdown targets the
// next `hard` one; soft milestones (briefing, lunch) are deliberately absent.
const timeline = [
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


// ------------------------------------------------------------- forecaster build
// What we are actually building, node by node, with status read from the repo
// rather than typed in. A node counts as built when its module exists; an agent
// counts as built when its prompt file exists, because the prompt file is the
// only definition of an agent that can be checked rather than asserted.

const MISSION = {
  goal:
    "Forecast twelve numbers - three metrics each for Home Depot, Analog Devices, Hays and Deere - "
    + "and show a visible trail from source evidence to every one of them.",
  thesis:
    "The forecast is consensus plus lambda times the gap between our own estimate and consensus. "
    + "Everything upstream produces the own estimate; lambda decides how much of it to act on. "
    + "Shrinking hard to consensus on a heavily covered name is the correct answer, not a failure.",
  scoring:
    "Accuracy is scored relative to Wall Street: min(5.0, our miss divided by the Street miss), averaged "
    + "over the twelve, lowest wins. A missing number scores 5.0, so we always ship a number. "
    + "Architecture is judged separately and rewards the evidence trail, validation and stated "
    + "weaknesses - explicitly not complexity and not accuracy.",
  approach: [
    "Filings, transcripts and slides come from the frozen 1,139-document corpus in this repo. No retrieval agent, and every number traces to a document a judge can open.",
    "Historic financials are read from those same filings, not from a market-data API.",
    "Analyst consensus comes from the network, because it is a forward estimate the corpus never contained and it is the number our error is scored against.",
    "Nine lenses argue the case independently and are blind to each other. Lenses that see each other converge, and converging rebuilds consensus, which scores zero.",
    "Every cited quote is string-matched against its document. A lens that fails is dropped whole, and the drop is shown rather than hidden.",
    "One deep-tier call makes the judgement, weighting by materiality rather than by vote count.",
    "Python writes JSON; the site polls it and computes nothing."
  ],
  cuts: [
    ["Balance sheet, cash flow, DCF", "None of the twelve metrics touches a balance-sheet or cash-flow line, so an income-statement model reaches every scored number and the circularity solve reaches none."],
    ["SEC XBRL as the history source", "Written and working, then set aside: the repo's own filings are the sanctioned evidence and give a trail that can be opened rather than trusted."],
    ["Backtest driver and eval harness", "Consensus is available only as of today, with no vintage, so the historical Street bar cannot be reconstructed. Skill-versus-consensus is unobtainable, not merely unbuilt."],
    ["Bootstrap calibration", "No backtest residuals exist to bootstrap. The interval is the judge's own and is labelled uncalibrated."],
    ["Perception scoring and the news adapter", "Coverage stance moves a discount rate, and none of the twelve metrics is a valuation."],
    ["Fixture generator", "A real run exists, and the demo fallback is a recorded run replayed through the same code path."],
    ["Options and implied move", "No entitlement on the available plan."]
  ]
};

const PIPELINE_PLAN = [
  { id: "A", label: "Sources - corpus first, consensus behind it", file: "forecaster/stages/a_sources.py",
    detail: "One protocol, priority chain, two independent point-in-time locks." },
  { id: "B", label: "Acquire - dossier keyed by ticker and as-of", file: "forecaster/stages/b_acquire.py",
    detail: "Results releases picked by form, never by date. Eight earnings calls kept as an ordered sequence." },
  { id: "C", label: "Evidence store - citation IDs", file: "forecaster/stages/c_structure.py",
    detail: "Prose quoted and matched, structured facts typed by the adapter, derived figures marked as neither." },
  { id: "D", label: "Income model - ratio base and reproduction check", file: "forecaster/stages/d_model.py",
    detail: "History as filed, median and scaled MAD per driver, and the model stating its own measured error before it is trusted." },
  { id: "E", label: "Nine lenses, blind to each other", file: "forecaster/stages/e_analyse.py",
    detail: "Eight on the mid tier plus Mechanical, which has no model and cannot hallucinate." },
  { id: "V1", label: "Reconcile - quote matching", file: "forecaster/stages/v1_reconcile.py",
    detail: "Deterministic. Nothing that can hallucinate decides which hallucinations survive." },
  { id: "F", label: "Champion - argue, attack, survive, weigh", file: "forecaster/stages/f_challenge.py",
    detail: "Self-reported confidence replaced by what held up under attack." },
  { id: "G", label: "Judge - one deep call", file: "forecaster/stages/g_judge.py",
    detail: "Weighted by materiality, told which lenses are missing and why, returning five named quantiles." },
  { id: "V2", label: "Comparability", file: "forecaster/stages/v2_comparability.py",
    detail: "M&A, accounting change, 53rd week, withdrawn guidance. When one fires, lambda collapses." },
  { id: "H", label: "Position - lambda and the consensus bus", file: "forecaster/stages/h_position.py",
    detail: "Consensus is tapped once and carried untouched to lambda's second input; the baseline terminates on it." },
  { id: "I", label: "Output - results file and event tape", file: "forecaster/stages/i_output.py",
    detail: "Written atomically. Every field any sheet renders lives here." },
  { id: "RUN", label: "Flat orchestrator", file: "forecaster/run.py",
    detail: "Every stage in order, one level of indentation, readable out loud." },
  { id: "XL", label: "Results into the four workbooks", file: "src/forecast-to-workbooks.mjs",
    detail: "Patches the supplied templates in place, asserting labels, units and period first." },
  { id: "SITE", label: "Astro site, nine sheets", file: "site/package.json",
    detail: "Polls the results file and the tape. Renders and nothing more." }
];

const AGENT_PLAN = [
  ["lens_mechanical", "Mechanical - FX, share count, calendar effects. No model, zero tokens."],
  ["lens_guidance", "Guidance - the guided range and where the company lands inside it."],
  ["lens_drivers", "Drivers - units times price, comps times stores, backlog conversion."],
  ["lens_demand", "Demand - one link up and one link down the value chain."],
  ["lens_market", "Market - market growth separated from share change."],
  ["lens_margins", "Margins - gross margin mix, operating expense, tax."],
  ["lens_forensics", "Forensics - accruals and the GAAP-to-adjusted exclusions."],
  ["lens_peer_read", "Peer read - who has already reported into this cycle."],
  ["lens_macro", "Macro - the series that matter against what estimates assume."],
  ["extract_guidance", "Guidance extracted from the results release, carrying its quote."],
  ["extract_bridge", "The GAAP-to-adjusted bridge across five quarters."],
  ["scan_calls", "Reads eight calls at once and reports what management stopped saying."],
  ["scan_perception", "Scores the analyst Q&A across those calls for stance and conviction - our sentiment read, since the corpus carries no news."],
  ["champion", "Argues each case at its strongest, then attacks it in good faith."],
  ["judge", "The one deep call. Materiality, never vote count."],
  ["comparability", "Is this period comparable to its own history?"]
];

function forecasterFileExists(relative) {
  return fsSync.existsSync(path.join(root, relative));
}

async function pipelineSection() {
  return PIPELINE_PLAN.map((node) => ({
    id: "node:" + node.id,
    label: node.id + " - " + node.label,
    detail: node.detail,
    kind: "auto",
    status: forecasterFileExists(node.file) ? "done" : "todo",
  }));
}

async function agentSection() {
  return AGENT_PLAN.map((entry) => {
    const name = entry[0];
    const detail = entry[1];
    const built = name === "lens_mechanical"
      ? forecasterFileExists("forecaster/stages/e_analyse.py")
      : forecasterFileExists("llm/prompts/" + name + ".md");
    return {
      id: "agent:" + name,
      label: name === "lens_mechanical" ? name + " - deterministic" : name,
      detail,
      kind: "auto",
      status: built ? "done" : "todo",
    };
  });
}

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
      id: "pipeline",
      title: "Forecaster pipeline",
      subtitle: "Every stage of the signal flow. A node counts as built when its module exists - computed, never ticked by hand.",
      items: await pipelineSection(),
    },
    {
      id: "agents",
      title: "Agent roster",
      subtitle: "Sixteen prompts plus one deterministic lens. An agent counts as built when its prompt file exists, because the prompt file is the only definition that can be checked rather than asserted.",
      items: await agentSection(),
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
    mission: MISSION,
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
