// Fill a supplied OpenStocks template by patching the Summary sheet XML in place.
//
// The three forecast cells arrive from the template as empty styled stubs, e.g.
//   <x:c r="C7" s="34" />
// and we rewrite exactly those three into
//   <x:c r="C7" s="34"><x:v>45000</x:v></x:c>
// keeping the style index so the yellow fill and number format survive. Every
// other ZIP entry is copied through untouched. See src/zip.mjs for why we do not
// simply re-serialise with SheetJS.
//
// Before writing anything we re-derive the header row and assert the metric
// labels, units and fiscal-period header still match challenge/companies.json.
// If the organisers' template and the spec ever disagree we want a loud failure,
// not a workbook with a forecast parked against the wrong metric.

import fs from "node:fs";
import { readZip, writeZip } from "./zip.mjs";

const SUMMARY_SHEET = "Summary";
const HEADER_LABELS = ["Metric", "Units"];

export function fillWorkbook({ templatePath, outputPath, company, values }) {
  if (values.length !== company.metrics.length) {
    throw new Error(
      `${company.ticker}: expected ${company.metrics.length} values, received ${values.length}`,
    );
  }
  values.forEach((value, index) => {
    if (typeof value !== "number" || !Number.isFinite(value)) {
      throw new Error(
        `${company.ticker}: "${company.metrics[index].label}" is ${value}, which is not a finite number`,
      );
    }
  });

  const entries = readZip(fs.readFileSync(templatePath));
  const sheetPath = resolveSheetPath(entries, SUMMARY_SHEET);
  const sheetEntry = entries.find((entry) => entry.name === sheetPath);
  let xml = sheetEntry.data.toString("utf8");

  const strings = readSharedStrings(entries);
  const cells = parseCells(xml, strings);
  const headerRow = findHeaderRow(cells, company.period);

  const patches = company.metrics.map((metric, index) => {
    const row = headerRow + index + 1;
    assertCellText(cells, `A${row}`, metric.label, company);
    assertCellText(cells, `B${row}`, metric.units, company);

    const reference = `C${row}`;
    const target = cells.get(reference);
    if (!target) {
      throw new Error(`${company.ticker}: forecast cell ${reference} is missing from the template`);
    }
    return { reference, style: target.style, value: values[index], label: metric.label };
  });

  for (const patch of patches) {
    xml = replaceCell(xml, patch.reference, patch.style, patch.value, company.ticker);
  }

  sheetEntry.data = Buffer.from(xml, "utf8");
  fs.writeFileSync(outputPath, writeZip(entries));

  return {
    outputPath,
    sheetPath,
    headerRow,
    written: patches.map(({ label, reference, value }) => ({ label, cell: reference, value })),
  };
}

/** Read a completed workbook back as label -> number, used for the post-write check. */
export function readForecasts(workbookPath) {
  const entries = readZip(fs.readFileSync(workbookPath));
  const sheetPath = resolveSheetPath(entries, SUMMARY_SHEET);
  const xml = entries.find((entry) => entry.name === sheetPath).data.toString("utf8");
  const cells = parseCells(xml, readSharedStrings(entries));

  const forecasts = new Map();
  for (const [reference, cell] of cells) {
    const match = /^A(\d+)$/.exec(reference);
    if (!match || typeof cell.text !== "string" || !cell.text) continue;
    const value = cells.get(`C${match[1]}`);
    if (value && value.number !== undefined) forecasts.set(cell.text, value.number);
  }
  return forecasts;
}

function resolveSheetPath(entries, sheetName) {
  const workbookXml = entries.find((entry) => entry.name === "xl/workbook.xml").data.toString("utf8");
  const sheetTag = new RegExp(`<[^>]*sheet\\b[^>]*name="${sheetName}"[^>]*>`).exec(workbookXml);
  if (!sheetTag) throw new Error(`workbook has no sheet named "${sheetName}"`);

  const relationshipId = /r:id="([^"]+)"/.exec(sheetTag[0])?.[1];
  if (!relationshipId) throw new Error(`sheet "${sheetName}" has no relationship id`);

  const relsXml = entries
    .find((entry) => entry.name === "xl/_rels/workbook.xml.rels")
    .data.toString("utf8");
  const relationship = new RegExp(`<[^>]*Id="${relationshipId}"[^>]*>`).exec(relsXml);
  const target = /Target="([^"]+)"/.exec(relationship?.[0] ?? "")?.[1];
  if (!target) throw new Error(`relationship ${relationshipId} has no target`);

  const normalised = target.replace(/^\//, "");
  return normalised.startsWith("xl/") ? normalised : `xl/${normalised}`;
}

function readSharedStrings(entries) {
  const entry = entries.find((item) => item.name === "xl/sharedStrings.xml");
  if (!entry) return [];
  return [...entry.data.toString("utf8").matchAll(/<[^>]*\bsi\b[^>]*>([\s\S]*?)<\/[^>]*\bsi\b>/g)].map(
    (match) =>
      [...match[1].matchAll(/<[^>]*\bt\b[^>]*>([\s\S]*?)<\/[^>]*\bt\b>/g)]
        .map((part) => decodeXml(part[1]))
        .join(""),
  );
}

function parseCells(xml, strings) {
  const cells = new Map();
  for (const match of xml.matchAll(/<[^>]*\bc\b\s([^>]*?)(\/>|>([\s\S]*?)<\/[^>]*\bc\b>)/g)) {
    const attributes = match[1];
    const body = match[3] ?? "";
    const reference = /\br="([^"]+)"/.exec(attributes)?.[1];
    if (!reference) continue;

    const style = /\bs="([^"]+)"/.exec(attributes)?.[1];
    const type = /\bt="([^"]+)"/.exec(attributes)?.[1];
    const rawValue = /<[^>]*\bv\b[^>]*>([\s\S]*?)<\/[^>]*\bv\b>/.exec(body)?.[1];

    let text;
    let number;
    if (rawValue !== undefined) {
      if (type === "s") text = strings[Number(rawValue)];
      else if (type === "str" || type === "inlineStr") text = decodeXml(rawValue);
      else if (Number.isFinite(Number(rawValue))) number = Number(rawValue);
    }
    cells.set(reference, { style, text, number });
  }
  return cells;
}

function findHeaderRow(cells, period) {
  for (let row = 1; row <= 30; row += 1) {
    const a = cells.get(`A${row}`)?.text?.trim();
    const b = cells.get(`B${row}`)?.text?.trim();
    const c = cells.get(`C${row}`)?.text?.trim();
    if (a === HEADER_LABELS[0] && b === HEADER_LABELS[1] && c === period) return row;
  }
  throw new Error(`template has no "Metric / Units / ${period}" header row`);
}

function assertCellText(cells, reference, expected, company) {
  const actual = cells.get(reference)?.text?.trim();
  if (actual !== expected) {
    throw new Error(
      `${company.ticker}: template cell ${reference} reads "${actual}" but companies.json expects "${expected}"`,
    );
  }
}

function replaceCell(xml, reference, style, value, ticker) {
  const styleAttribute = style === undefined ? "" : ` s="${style}"`;
  const pattern = new RegExp(`<(\\w*:?)c\\s[^>]*\\br="${reference}"[^>]*?(?:/>|>[\\s\\S]*?</\\1?c>)`);
  const match = pattern.exec(xml);
  if (!match) throw new Error(`${ticker}: could not locate cell ${reference} in the sheet XML`);

  const prefix = match[1] ?? "";
  const replacement = `<${prefix}c r="${reference}"${styleAttribute}><${prefix}v>${value}</${prefix}v></${prefix}c>`;
  return xml.slice(0, match.index) + replacement + xml.slice(match.index + match[0].length);
}

function decodeXml(text) {
  return text
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&quot;/g, '"')
    .replace(/&apos;/g, "'")
    .replace(/&#(\d+);/g, (_, code) => String.fromCodePoint(Number(code)))
    .replace(/&amp;/g, "&");
}
