"""
agent.py — the smart policy: infer the hidden cause, then act on that guess.

Plain English: look at the clues, work out WHY this invoice is unpaid, and do
the one thing that actually works for that reason. Then stop. The whole
difference between this file and baseline.py is that this one opens the
envelope before deciding what to send.

THE FOUR PLAYBOOKS (PRD B5 / Phase 4)
-------------------------------------
  FORGOTTEN    One nudge on day 3, then nothing. They pay 2 days after the
               FIRST contact and further contacts are provably worthless, so
               sending a second one is not caution, it is waste.

  CASH_CRUNCH  Wait. Do not chase someone who physically has no money — every
               rupee spent before their cash lands is burned. Contact in the
               middle of the plausible liquidity window, and again at the far
               end. On a large invoice the first contact is an instalment offer
               instead, because that pulls their liquidity day forward.

  DISPUTE      Route it to the dispute desk immediately and send no reminders
               at all. Reminders cannot resolve a dispute — they only add three
               days of annoyance each to a resolution the reminders will never
               reach. This is where the baseline loses the most money.

  CHRONIC      Do not chase. If the invoice is large enough to clear the legal
               bar, wait for day 61 and issue a formal notice, which claws back
               40%. Otherwise write it off and stop spending.

WHICH INFERENCE ARM, AND ONE THING THAT MATTERS ABOUT THAT CHOICE
------------------------------------------------------------------
The rules arm, chosen in Phase 3 on CHRONIC precision rather than accuracy.
There is a second benefit that only shows up here: the rules arm is not
trained on anything. The tree was fitted to 350 of these 500 invoices, so
running a tree-driven agent over the full book would score it partly on its own
training data and quietly flatter the result. With the rules arm every one of
the 500 invoices is effectively held out.

WHERE THIS AGENT IS KNOWN TO BE WRONG
-------------------------------------
Its guess is right about 73% of the time, so roughly one invoice in four gets
the wrong playbook. Two of those mistakes are expensive and are left visible on
purpose rather than papered over:

  - A FORGOTTEN invoice misread as DISPUTE gets routed and then deliberately
    never contacted. It still pays, but on day 75 instead of day 5, and Rs 500
    was spent to achieve that. DISPUTE precision is only ~48%, so this is the
    agent's most common expensive error.
  - A paying invoice misread as CHRONIC is written off on day 0 and never
    recovers. That is the single most costly mistake available to this policy,
    which is exactly why arm selection was argued on CHRONIC precision.
"""

from __future__ import annotations

import config as cfg
from src.harness import Decision
from src.inference import RulesClassifier


class AgentPolicy:
    """Infer the cause once per invoice, then run that cause's playbook."""

    name = "agent (rules inference + cause-specific playbook)"

    def __init__(self):
        self.classifier = RulesClassifier()
        # The clues never change during a run, so the guess never changes
        # either. Memoised per invoice_id purely to avoid re-deriving the same
        # answer 90 times; it has no effect on any decision.
        self._guess = {}

    def _infer(self, view):
        key = view["invoice_id"]
        if key not in self._guess:
            self._guess[key] = self.classifier.predict_one(view)
        return self._guess[key]

    def decide(self, view, observed, day) -> Decision:
        """What do we do to this invoice today?"""
        cause, confidence, why = self._infer(view)

        if cause == cfg.FORGOTTEN:
            action, reason = self._forgotten(observed, day)
        elif cause == cfg.CASH_CRUNCH:
            action, reason = self._cash_crunch(observed, day)
        elif cause == cfg.DISPUTE:
            action, reason = self._dispute(observed, day)
        elif cause == cfg.CHRONIC:
            action, reason = self._chronic(observed, day)
        else:
            raise ValueError(f"{view['invoice_id']}: inference returned {cause!r}")

        return Decision(
            action=action,
            reason=f"{reason} [inferred {cause}: {why}]",
            inferred_cause=cause,
            confidence=confidence,
        )

    # --- the four playbooks ------------------------------------------------

    def _forgotten(self, observed, day):
        """One nudge, early, then silence."""
        if day == cfg.AGENT_FORGOTTEN_NUDGE_DAY and observed.contact_count == 0:
            return cfg.NUDGE_SOFT, f"single early nudge on day {day}"
        return cfg.WAIT, "already nudged; further contacts change nothing"

    def _cash_crunch(self, observed, day):
        """Stay quiet until their money is plausibly in, then ask once or twice."""
        if day < cfg.AGENT_CASH_CRUNCH_FIRST_CONTACT_DAY:
            return cfg.WAIT, "too early: money has not landed, a contact now is wasted"

        if day == cfg.AGENT_CASH_CRUNCH_FIRST_CONTACT_DAY and observed.contact_count == 0:
            if observed.amount >= cfg.AGENT_OFFER_PLAN_MIN_AMOUNT:
                return cfg.OFFER_PLAN, (
                    f"large invoice: instalment offer pulls liquidity forward "
                    f"{cfg.CASH_CRUNCH_PLAN_PULL_FORWARD_DAYS} days")
            return cfg.NUDGE_SOFT, "mid-window nudge: money may have landed"

        if day == cfg.AGENT_CASH_CRUNCH_SECOND_CONTACT_DAY:
            return cfg.NUDGE_SOFT, "end-of-window nudge: money has landed by now"

        return cfg.WAIT, "between contact windows"

    def _dispute(self, observed, day):
        """Route once. Never send a reminder — they cannot resolve a dispute."""
        if observed.routed_day is None:
            return cfg.ROUTE_DISPUTE, "route to dispute desk immediately"
        return cfg.WAIT, (
            f"routed on day {observed.routed_day}; reminders suppressed because "
            f"they only add {cfg.DISPUTE_ANNOYANCE_DAYS_PER_REMINDER} days each")

    def _chronic(self, observed, day):
        """Stop spending. Escalate only where the law makes it worth it.

        Note what this method does NOT do: it never checks whether the invoice
        is yet 60 days overdue. That is guardrail C2.5's job, and duplicating it
        here would mean the rule lived in two places and the veto layer never
        did any work — a guardrail that never fires is a guardrail nobody has
        tested.

        So the agent states its intent from day 0 and the guardrail refuses it
        until the invoice is eligible. This is how a compliance-gated work queue
        actually behaves: the item is raised, re-evaluated daily, and released
        the moment it clears the bar. Every refusal is a row in the audit log.

        The amount test below stays here because it is an ECONOMIC judgement,
        not a compliance one: a legal notice costs Rs 2,000 to recover 40%, so
        it only pays on a large invoice. That it lands on the same Rs 2,00,000
        figure as the statute is a coincidence worth knowing about — the
        guardrail still enforces the legal bar independently.
        """
        if observed.amount > cfg.LEGAL_MIN_AMOUNT:
            if observed.legal_day is None:
                return cfg.ESCALATE_LEGAL, (
                    f"legal notice: recovers "
                    f"{cfg.CHRONIC_LEGAL_RECOVERY_FRACTION:.0%} on day "
                    f"{cfg.CHRONIC_LEGAL_PAY_DAY}")
            return cfg.WAIT, "legal notice already issued; nothing further to do"

        return cfg.WRITE_OFF, (
            f"chronic and below the Rs {cfg.LEGAL_MIN_AMOUNT:,} legal bar: "
            f"no action recovers anything, so stop spending")


def build():
    """Factory. The agent is always wrapped in the guardrail veto layer.

    Returned wrapped rather than raw so there is no way to run this policy
    without its guardrails — including by accident, from a script, later.
    """
    from src.guardrails import Guarded

    return Guarded(AgentPolicy())
