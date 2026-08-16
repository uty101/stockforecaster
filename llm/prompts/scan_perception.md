---
name: "scan_perception"
version: "1"
tier: "cheap"
layer: "03 E expect"
node: "U9"
summary: "Scores the stance and conviction of the analyst Q&A across recent calls."
---

You are reading the analyst question-and-answer sections of {{company}}'s ({{ticker}})
recent calls, and any retrieved coverage, to judge how the market is currently
positioned on this name.

Score **stance** and **conviction** separately, and do not confuse them.

Stance is direction: are questioners probing for downside, testing an upside case,
or neutral. Conviction is how settled the view appears: a room asking the same
sceptical question five different ways is not the same as a room that asks once and
moves on.

Do not count polarity. Counting how many positive and negative mentions there are
measures publication volume and question count, and coverage spikes before every
print regardless of direction. What you are reading for is *what the market thinks
it already knows*, and where it is unsure.

Name the two or three things questioners keep returning to. Those are where the
market's uncertainty actually sits, and they are more useful than any overall
score.

## What this is for, and what it is not for

This read moves uncertainty. It widens or narrows how confident the system should
be, and nothing else.

It must never reach a revenue or a cost driver. Sentiment feeding a revenue line is
how a forecast ends up restating the loudest recent headline, and it is guaranteed
structurally here: nothing downstream passes your output into the driver path. Do
not offer a revenue or margin estimate, because there is nowhere for one to go.
