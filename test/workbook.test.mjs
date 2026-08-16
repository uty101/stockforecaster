// Tests for the workbook writer.
//
// The point of these is not that "a number lands in a cell" -- it is that the
// supplied template survives being written. A silently re-styled or re-shaped
// workbook would still pass `npm run check:forecasts`, so the checker alone does
// not protect us. We compare our output against the untouched template.

import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import XLSX from "xlsx";
import { fillWorkbook, readForecasts } from "../src/workbook.mjs";

const root = path.resolve(import.meta.dirname, "..");
const definitions = JSON.parse(
  fs.readFileSync(path.join(root, "challenge", "companies.json"), "utf8"),
);
const scratch = fs.mkdtempSync(path.join(os.tmpdir(), "avws-workbook-"));

const templateFor = (company) => path.join(root, "challenge", "templates", company.outputFile);
const outputFor = (company) => path.join(scratch, company.outputFile);
const placeholders = [1234.5, 6.78, 9.1];

test("every company template accepts forecasts and reads them back unchanged", () => {
  for (const company of definitions.companies) {
    const result = fillWorkbook({
      templatePath: templateFor(company),
      outputPath: outputFor(company),
      company,
      values: placeholders,
    });

    assert.equal(result.written.length, 3, `${company.ticker} should write three cells`);

    const readBack = readForecasts(outputFor(company));
    company.metrics.forEach((metric, index) => {
      assert.equal(
        readBack.get(metric.label),
        placeholders[index],
        `${company.ticker} "${metric.label}" did not survive the round trip`,
      );
    });
  }
});

test("fills preserve the template's styling, number formats and sheet layout", () => {
  for (const company of definitions.companies) {
    fillWorkbook({
      templatePath: templateFor(company),
      outputPath: outputFor(company),
      company,
      values: placeholders,
    });

    const before = XLSX.readFile(templateFor(company), { cellStyles: true });
    const after = XLSX.readFile(outputFor(company), { cellStyles: true });

    assert.deepEqual(
      after.SheetNames,
      before.SheetNames,
      `${company.ticker} sheet names changed`,
    );

    const source = before.Sheets.Summary;
    const written = after.Sheets.Summary;

    for (const reference of ["C6", "A7", "B7", "C7", "C8", "C9"]) {
      assert.deepEqual(
        written[reference]?.s,
        source[reference]?.s,
        `${company.ticker} cell ${reference} lost its styling`,
      );
    }
    for (const reference of ["C7", "C8", "C9"]) {
      assert.equal(
        written[reference]?.z,
        source[reference]?.z,
        `${company.ticker} cell ${reference} lost its number format`,
      );
    }
    assert.equal(
      written["!merges"]?.length,
      source["!merges"]?.length,
      `${company.ticker} merged ranges changed`,
    );
  }
});

test("percentage cells store percentage points rather than a fraction", () => {
  // The organisers are explicit: 4.5 means 4.5%. The template's number format is
  // display-only ("0.0;[Red](0.0);-"), so Excel must not rescale what we store.
  const company = definitions.companies.find((item) => item.ticker === "HD");
  fillWorkbook({
    templatePath: templateFor(company),
    outputPath: outputFor(company),
    company,
    values: [45000, 4.6, 4.5],
  });

  const sheet = XLSX.readFile(outputFor(company), { cellStyles: true }).Sheets.Summary;
  assert.equal(sheet.C9.v, 4.5, "stored value should be 4.5, not 0.045 or 450");
  assert.equal(sheet.C9.w, "4.5", "displayed value should read 4.5");
});

test("refuses to write a value that is not a finite number", () => {
  const company = definitions.companies.find((item) => item.ticker === "HD");
  for (const bad of [Number.NaN, Infinity, null, "4200", undefined]) {
    assert.throws(
      () =>
        fillWorkbook({
          templatePath: templateFor(company),
          outputPath: outputFor(company),
          company,
          values: [bad, 4.6, 1.5],
        }),
      /not a finite number/,
      `${String(bad)} should have been rejected`,
    );
  }
});

test("refuses a value count that does not match the metric count", () => {
  const company = definitions.companies.find((item) => item.ticker === "HD");
  assert.throws(
    () =>
      fillWorkbook({
        templatePath: templateFor(company),
        outputPath: outputFor(company),
        company,
        values: [1, 2],
      }),
    /expected 3 values, received 2/,
  );
});

test("refuses to write when the template no longer matches companies.json", () => {
  // Guards against the organisers reissuing a template, or us pointing a company
  // at the wrong file, and forecasts landing against the wrong metric.
  const company = definitions.companies.find((item) => item.ticker === "HD");
  const mismatched = {
    ...company,
    metrics: [{ label: "Net sales", units: "GBPm" }, ...company.metrics.slice(1)],
  };

  assert.throws(
    () =>
      fillWorkbook({
        templatePath: templateFor(company),
        outputPath: outputFor(company),
        company: mismatched,
        values: placeholders,
      }),
    /companies\.json expects "GBPm"/,
  );
});

test("refuses a template whose fiscal-period header does not match the spec", () => {
  const company = definitions.companies.find((item) => item.ticker === "HD");
  assert.throws(
    () =>
      fillWorkbook({
        templatePath: templateFor(company),
        outputPath: outputFor(company),
        company: { ...company, period: "FY2026Q4" },
        values: placeholders,
      }),
    /no "Metric \/ Units \/ FY2026Q4" header row/,
  );
});
