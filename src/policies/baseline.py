"""
baseline.py — the dumb opponent: a fixed dunning ladder, identical for everyone.

Plain English: send a polite email on day 7. Send a firmer one on day 21. Phone
them on day 45. Do this to every single customer, regardless of who they are or
why they have not paid. This is what most receivables teams actually run today,
and it is the number the agent has to beat.

WHY THIS IS THE RIGHT OPPONENT
------------------------------
It is stupid in exactly one way — it never asks *why* an invoice is unpaid — and
it is competent in every other way. It does not spam, it does not chase forever,
and (via the harness world rail) it respects opt-outs. That matters: if we beat
a baseline that was also badly implemented, we have proved nothing except that
we can write worse code on purpose.

Look at what `decide` reads: nothing. Not `view`, not `observed`. That signature
takes both arguments only because the harness hands every policy the same thing.
The whole difference between this file and the Phase 4 agent is that the agent
actually opens the envelope.

WHAT WE EXPECT TO SEE IN THE PER-CAUSE TABLE (the Phase 2 gate)
---------------------------------------------------------------
  FORGOTTEN    recovers nearly everything, but slowly — nothing happens until
               day 7, so cash that could have landed on day 5 lands on day 9.
  CASH_CRUNCH  recovers only where the ladder's day-45 call happens to fall on
               or after the customer's hidden liquidity day. The day 7 and day
               21 emails are pure waste — the money did not exist yet.
  DISPUTE      recovers Rs 0. Reminders cannot resolve a dispute, ever. Worse,
               each one adds three days of annoyance to a resolution that this
               policy is never going to reach anyway.
  CHRONIC      recovers Rs 0 and pays full contact cost for the privilege.

If the table does not look like that, the world model is wrong — not the
baseline. Fix the world model.
"""

from __future__ import annotations

import config as cfg
from src.harness import Decision

# The ladder, in one place: day number -> action. PRD C1.
LADDER = {
    7: cfg.NUDGE_SOFT,
    21: cfg.NUDGE_FIRM,
    45: cfg.CALL,
}


class BaselinePolicy:
    """The fixed ladder. No inference, no judgement, no stopping."""

    name = "baseline (fixed ladder: soft d7, firm d21, call d45)"

    def decide(self, view, observed, day):
        """What do we do to this invoice today?

        Reads only the calendar. `view` and `observed` are accepted and ignored
        — that is the entire point of the control arm. `inferred_cause` stays
        None on every row of the audit log, which is the honest record of a
        policy that never forms a view about anything.
        """
        action = LADDER.get(day)
        if action is None:
            return Decision(cfg.WAIT, f"day {day}: not a ladder day")
        return Decision(action, f"day {day}: fixed ladder step")


def build() -> BaselinePolicy:
    """Factory, so run.py builds every policy the same way."""
    return BaselinePolicy()
