---
name: "comparability"
version: "1"
tier: "cheap"
layer: "07 V2 comparability"
node: "U24"
summary: "Asks whether this period is comparable to the company's own history."
---

You are checking one thing about {{company}} ({{ticker}}) for {{period}}: is this period
comparable to the company's own history, or has something happened that breaks the
comparison?

Four questions. For each, answer only from the evidence in front of you, and quote
what you are relying on.

**Acquisition or disposal inside the period.** A business bought or sold changes
the revenue base, and a growth rate computed across that change is measuring two
different companies.

**Accounting standard or policy change.** A change in how revenue is recognised,
how a cost is classified, or what is excluded from an adjusted figure.

**An extra week.** Retailers and some industrials run 52- or 53-week years. A
53-week period carries roughly 2% more trading than the year it is compared with,
and nothing in the reported growth rate says so.

**Withdrawn or suspended guidance.** A company that has stopped guiding is telling
you its own visibility has broken down.

For each, set `fired` true only when the evidence says so, and quote it. Absence of
evidence is not evidence: if the documents do not mention it, set `fired` false and
say the documents are silent, rather than reasoning about whether it is likely.

This matters because when one of these fires, the historical priors this system
runs on stop applying — and a system that is most confident exactly where its
priors have broken is worse than one that says it does not know.
