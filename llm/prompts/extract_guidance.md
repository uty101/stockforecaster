---
name: "extract_guidance"
version: "1"
tier: "cheap"
layer: "02 B acquire"
node: "U4"
summary: "Reads the most recent results release only and returns forward guidance ranges, each carrying a verbatim quote."
---

You are reading the most recent results release published by {{company}}
({{ticker}}), and you are looking for one thing: what the company has told the
market to expect for a period that has **not yet been reported**.

## What counts

Forward guidance. A range, a point estimate, an outlook, a reaffirmation of a
previously given range, or an explicit withdrawal of guidance. It counts whether
management framed it as guidance, an outlook, an expectation or a target.

## What does not count

Anything the company reported as an actual result for a period that has already
ended. Those are somebody else's job, and mixing them in here is the single most
damaging thing you could do, because a reported actual dressed as guidance will be
read downstream as the bar the company set for itself.

Guidance for a period that has already reported is stale and does not count. A
range issued for a quarter that has since been announced hands the pipeline a
number that looks current and is not.

## Rules

Every guidance item carries a verbatim quote, copied character for character. Do
not join sentences, do not tidy, do not paraphrase. The quote is string-matched
against this document and an item whose quote cannot be found is dropped.

For a range, report `low` and `high` and leave `point` null. For a point estimate
or a single-number target, report `point` and leave `low` and `high` null. For a
withdrawal, set `withdrawn` true and leave the numbers null.

`metric_label` names what is being guided in the company's own words — "net sales
growth", "adjusted diluted earnings per share", "comparable sales". `period`
names the period being guided, also in the company's words.

`basis` records adjusted, GAAP, comparable, pre-exceptional or unstated. Guidance
given on an adjusted basis and read as GAAP is a silent error worth several
percent.

If this document contains no forward guidance at all, return an empty list. That
is a real and useful answer. Do not manufacture an outlook from management's tone.
