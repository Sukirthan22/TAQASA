"""
guardrails.py — the veto layer. Hard rules that overrule any policy, always.

Plain English: the agent decides what it WANTS to do. This file decides what it
is ALLOWED to do. When the two disagree, this file wins and the disagreement is
written into the audit log.

WHY IT IS A WRAPPER AND NOT AN `if` INSIDE THE AGENT
-----------------------------------------------------
Because a guardrail you can forget to call is not a guardrail. The wrapper sits
between the harness and the policy, so every decision passes through it whether
the policy remembers it exists or not. It also wraps ANY policy — you could
wrap the dumb baseline in it and the rules would bind identically. Compliance
is not supposed to be a property of being clever.

A veto is never a silent correction. The requested action is replaced with WAIT
and the original intent is preserved in the reason string, so the audit log
records what the agent wanted, not just what it was permitted to do.

THE RULES (PRD C2) AND WHERE EACH ONE LIVES
-------------------------------------------
  1. Max 4 contacts per invoice, ever ................... here
  2. Minimum 48 hours between contacts .................. here
  3. Halt on promise-to-pay until the promised date ..... here
  4. Halt permanently on customer opt-out ............... here (harness backstops)
  5. ESCALATE_LEGAL needs > Rs 2L AND > 60 days overdue . here
  6. WRITE_OFF and PAID are terminal .................... world model + harness
  7. 90-day horizon, then write off ..................... harness

Rules 6 and 7 are physics, not policy — an action after PAID is not a
compliance breach, it is a contradiction, so the world model raises on it
instead of vetoing. Rule 4 is enforced in both places on purpose: the agent is
expected to be compliant by choice, and the harness rail is the backstop that
catches it if it is not.
"""

from __future__ import annotations

import config as cfg
from src.harness import Decision


class Guarded:
    """Wraps a policy and vetoes anything PRD C2 forbids."""

    def __init__(self, policy):
        self.policy = policy
        self.name = f"{policy.name} + guardrails"

    def decide(self, view, observed, day) -> Decision:
        """Ask the policy what it wants, then decide whether it may have it."""
        decision = self.policy.decide(view, observed, day)
        veto = self._veto_reason(decision.action, view, observed, day)

        if veto is None:
            return decision

        return Decision(
            action=cfg.WAIT,
            reason=f"VETOED {decision.action} ({decision.reason})",
            inferred_cause=decision.inferred_cause,
            confidence=decision.confidence,
            guardrail=veto,
        )

    def _veto_reason(self, action, view, observed, day):
        """Return the name of the rule that blocks this action, or None.

        Ordered most specific first, so the audit log names the rule a human
        would name. An ESCALATE_LEGAL on a small invoice is reported as the
        legal-threshold breach it is, not as a generic contact-cap breach.
        """
        # --- Rule 5: legal notice has a hard eligibility bar ----------------
        if action == cfg.ESCALATE_LEGAL:
            if not (observed.amount > cfg.LEGAL_MIN_AMOUNT
                    and observed.days_overdue > cfg.LEGAL_MIN_DAYS_OVERDUE):
                return (f"C2.5_legal_not_eligible:amount_{observed.amount:.0f}"
                        f"_overdue_{observed.days_overdue}")

        # Everything below governs reaching the customer. Actions that do not
        # reach them — WAIT, WRITE_OFF, and the internal ROUTE_DISPUTE — are
        # unconstrained by contact rules.
        if action not in cfg.CONTACT_ACTIONS:
            return None

        # --- Rule 4: they told us to stop ----------------------------------
        if observed.opted_out:
            return "C2.4_customer_opted_out"

        # --- Rule 3: they promised a date, so let it arrive -----------------
        if (observed.ptp_date is not None
                and not observed.ptp_broken
                and day <= observed.ptp_date):
            return f"C2.3_promise_to_pay_active_until_day_{observed.ptp_date}"

        # --- Rule 1: the contact budget is spent ---------------------------
        if observed.contact_count >= cfg.MAX_CONTACTS_PER_INVOICE:
            return f"C2.1_contact_cap_{cfg.MAX_CONTACTS_PER_INVOICE}_reached"

        # --- Rule 2: 48 hours between contacts -----------------------------
        gap = observed.days_since_last_contact
        if gap is not None and gap < cfg.MIN_DAYS_BETWEEN_CONTACTS:
            return f"C2.2_min_gap_{cfg.MIN_DAYS_BETWEEN_CONTACTS}d_only_{gap}d_since_last"

        return None
