---
name: "extract_bridge"
version: "1"
tier: "cheap"
layer: "02 B acquire"
node: "U7"
summary: "Extracts the GAAP-to-adjusted reconciliation across five periods."
---

You are reading {{company}}'s ({{ticker}}) results releases for one thing: the
reconciliation between the reported figure and the adjusted figure.

For each period, list every item the company added back or excluded, with the
amount and the label it used, each carrying a verbatim quote.

## Why five periods and not one

An item excluded once is a one-off. The same item excluded in four consecutive
periods is neither one-off nor, in any meaningful sense, a charge: it is a
permanent cost that has been moved below the line. That pattern is only visible
across periods, and this reconciliation is the only place it can be seen.

So when you find an item that recurs, say so explicitly in `recurring_items`, name
it, and say how many of the periods you looked at contain it.

## Rules

Quote verbatim. Do not tidy a label, do not merge two line items that the company
reported separately, and do not compute a total the company did not state.

Record the sign as the company presents it — an add-back is positive, an exclusion
that reduces the adjusted figure is negative — and if the direction is ambiguous
in the document, say `unstated` rather than inferring it.

If a release contains no reconciliation, return an empty list for that period.
Many do not, and that is a fact rather than a failure.
