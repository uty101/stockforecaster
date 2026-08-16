// Display formatting only. Nothing here derives a figure: every value handed to
// these functions was read out of a results file. The rules are the house
// number rules, applied in one place so a cell cannot drift from a header.

const DASH = "—";

function parens(text, negative) {
  return negative ? `(${text})` : text;
}

function group(value, dp) {
  return Math.abs(value).toLocaleString("en-GB", {
    minimumFractionDigits: dp,
    maximumFractionDigits: dp,
  });
}

export function isNum(value) {
  return typeof value === "number" && Number.isFinite(value);
}

// Zero renders as a dash, never as 0.0.
function zeroOrNull(value) {
  if (!isNum(value)) return DASH;
  if (value === 0) return DASH;
  return null;
}

// Money in millions: no decimals above 1,000, one decimal below.
export function money(value) {
  const early = zeroOrNull(value);
  if (early) return early;
  const abs = Math.abs(value);
  const dp = abs >= 1000 ? 0 : 1;
  return parens(group(value, dp), value < 0);
}

// Per share figures to 2 decimal places.
export function eps(value) {
  const early = zeroOrNull(value);
  if (early) return early;
  return parens(group(value, 2), value < 0);
}

// Percentage points, 1 decimal place. Stored and shown as points, never a fraction.
export function pct(value) {
  const early = zeroOrNull(value);
  if (early) return early;
  return parens(`${group(value, 1)}%`, value < 0);
}

// A change against a comparison reads better signed than in parentheses.
export function signedPct(value) {
  if (!isNum(value)) return DASH;
  if (value === 0) return DASH;
  return `${value > 0 ? "+" : "-"}${group(value, 1)}%`;
}

export function signed(value, dp = 2) {
  if (!isNum(value)) return DASH;
  if (value === 0) return DASH;
  return `${value > 0 ? "+" : "-"}${group(value, dp)}`;
}

// A signed figure carrying its own unit, so an absent value renders as one dash
// rather than as a dash with a percent sign after it.
export function signedPctPoints(value, dp = 2) {
  if (!isNum(value) || value === 0) return DASH;
  return `${signed(value, dp)}%`;
}

// Basis points, for a change in a margin.
export function bps(value) {
  if (!isNum(value)) return DASH;
  if (value === 0) return DASH;
  const points = Math.round(value * 100);
  return `${points > 0 ? "+" : "-"}${Math.abs(points).toLocaleString("en-GB")}bp`;
}

export function ratio(value) {
  const early = zeroOrNull(value);
  if (early) return early;
  return parens(group(value, 2), value < 0);
}

export function multiple(value) {
  if (!isNum(value)) return DASH;
  return `${group(value, 1)}x`;
}

// Position size to 3 decimal places.
export function position(value) {
  if (!isNum(value)) return DASH;
  return group(value, 3);
}

export function count(value) {
  if (!isNum(value)) return DASH;
  return Math.round(value).toLocaleString("en-GB");
}

export function usd(value, dp = 2) {
  if (!isNum(value)) return DASH;
  return `$${group(value, dp)}`;
}

export function tokens(value) {
  if (!isNum(value)) return DASH;
  if (value >= 1000000) return `${group(value / 1000000, 1)}m`;
  if (value >= 1000) return `${group(value / 1000, 1)}k`;
  return group(value, 0);
}

export function seconds(value) {
  if (!isNum(value)) return DASH;
  if (value >= 60) return `${group(value / 60, 1)}m`;
  return `${group(value, 1)}s`;
}

// The kind of thing a metric is decides how it reads.
export function kindOf(units = "") {
  const u = String(units).toLowerCase();
  if (u.includes("percent") || u === "pp" || u === "%") return "percent";
  if (u.includes("per share") || u === "usd" || u === "gbp" || u === "gbp_pence" || u === "pence" || u === "gbp pence") return "pershare";
  return "money";
}

export function metricValue(value, units) {
  const kind = kindOf(units);
  if (kind === "percent") return pct(value);
  if (kind === "pershare") return eps(value);
  return money(value);
}

// The unit belongs in the header or the eyebrow, never repeated in a cell.
export function unitLabel(units = "") {
  const u = String(units).toLowerCase();
  if (kindOf(units) === "percent") return "percentage points";
  if (u === "gbp_pence" || u === "pence" || u === "gbp pence") return "pence per share";
  if (u === "usdm") return "usd millions";
  if (u === "gbpm") return "gbp millions";
  if (u === "usd") return "usd per share";
  if (u === "gbp") return "gbp per share";
  return units || "";
}

// The gap between our figure and the Street, in the terms the metric is in.
export function gapLabel(own, other, units) {
  if (!isNum(own) || !isNum(other)) return DASH;
  const diff = own - other;
  if (kindOf(units) === "percent") return `${diff > 0 ? "+" : "-"}${group(diff, 1)} points`;
  if (other === 0) return DASH;
  return signedPct((diff / Math.abs(other)) * 100);
}

export function clockTime(ts) {
  if (!isNum(ts)) return DASH;
  const d = new Date(ts * 1000);
  const p = (n) => String(n).padStart(2, "0");
  return `${p(d.getUTCHours())}:${p(d.getUTCMinutes())}:${p(d.getUTCSeconds())}`;
}

export { DASH };
