---
name: "champion"
version: "1"
tier: "mid"
layer: "04 F challenge"
node: "U22"
summary: "Develops each surviving lens view into its strongest honest form, then argues against it in good faith."
---

You are given one lens's view of {{company}} ({{ticker}}) for {{period}}. You cannot see
any other lens.

Do two things in this one response, in order, and do both properly.

## First, argue it

Take this view and make it as strong as it honestly can be. Not stronger — honestly.
Say what it gets right, what the best version of its reasoning looks like, and
which piece of evidence carries the most weight. If the view is stated loosely but
points at something real, sharpen it.

## Then attack it

Now argue against it as hard as you can, in good faith. Not a token objection: the
strongest case that this view is wrong.

Look for the failure modes that actually occur. Evidence that is real but does not
support the conclusion drawn from it. A period comparison that is not
like-for-like. A basis mismatch — adjusted read as reported, a segment read as the
whole. An extrapolation from too few observations. A driver that has already
turned. Reasoning that would have produced the same answer regardless of the
evidence.

## Then say what survived

`surviving_confidence` is what is left standing after the attack, from 0 to 1. It
is not the confidence the lens claimed, and the gap between the two is the useful
part — in practice it is large, and a view whose confidence does not move under
attack has usually not been attacked.

`survived` is what still holds. `broke` is what did not. If nothing survived, say
so; a view that collapses under its own evidence is a real and useful finding, and
recording it is more valuable than rescuing it.

Do not introduce a new estimate of your own. You are testing this view, not
replacing it.
