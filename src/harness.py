"""
harness.py — the scoreboard. Runs any policy over every invoice and scores it.

Plain English: this file is the referee. It walks each invoice day by day from
day 0 to day 90, asks the policy "what do you want to do today?", hands that
action to the world model, and adds up what happened. It does the same job
identically for the dumb baseline and for the smart agent, which is the only
reason comparing the two means anything.

Built BEFORE either policy, on purpose. If you write the scoreboard after the
player, you write a scoreboard that flatters the player.

THREE THINGS THIS FILE IS RESPONSIBLE FOR
-----------------------------------------

1. THE LEAKAGE RAIL AT RUN TIME.
   `config.OBSERVABLE_COLUMNS` protects the CSV, but the live object the
   simulation passes around — `world_model.InvoiceState` — also carries
   `latent_cause` and `liquidity_day`. A policy handed that object could just
   read the answer. So the harness never gives a policy an `InvoiceState`. It
   builds an `ObservedState`: a redacted mirror holding only what a real
   collections team would actually know — what it sent, when, what came back.

2. THE WORLD RAILS.
   Two constraints belong to the world, not to anybody's cleverness:
     - A customer who has opted out cannot be contacted. Not "should not" —
       cannot. The harness converts any contact attempt into WAIT and records
       the suppression. This applies to BOTH policies, deliberately. A baseline
       that kept emailing people who said stop would burn extra cost and
       inflate the agent's lift with a compliance failure rather than with
       judgement. We refuse to buy lift that cheaply.
     - PAID and WRITTEN_OFF are terminal, and day 90 closes the book (PRD C2.6,
       C2.7). Anything still open at the horizon is written off.
   Everything else in PRD C2 is policy-side guardrail work and lands in Phase 4.

3. SCORING.
   The scorer is allowed to read `latent_cause` — it is the God's-eye view that
   produces the per-cause table. Policies are not. Keep that distinction in
   your head while reading: `_score` peeks, `decide` never does.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pandas as pd

import config as cfg
from src import simulator
from src.world_model import (
    InvoiceState,
    Outcome,
    PAID,
    WRITTEN_OFF,
    finalize_at_horizon,
    step,
)


# ---------------------------------------------------------------------------
# What a policy is allowed to see
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ObservedState:
    """The redacted mirror of an invoice's live state.

    Every field here is something a real receivables team would genuinely know
    on the morning of day N: what they have sent, when they sent it, whether the
    customer picked up, whether they promised a date, whether they told you to
    stop, and what the chasing has cost so far.

    Deliberately absent: `latent_cause`, `liquidity_day`, `ptp_will_promise`,
    `optout_after_contacts`. Those are the world model's private property.
    """

    invoice_id: str
    amount: float
    day: int
    status: str

    # --- your own chasing history ------------------------------------------
    actions_taken: tuple = ()          # ((day, action), ...) everything you did
    contact_days: tuple = ()           # days a real customer contact went out
    routed_day: Optional[int] = None
    legal_day: Optional[int] = None
    plan_offered_day: Optional[int] = None
    cost_so_far: float = 0.0

    # --- what came back from the customer ----------------------------------
    ptp_date: Optional[int] = None
    ptp_broken: bool = False
    opted_out: bool = False

    @property
    def days_overdue(self) -> int:
        """Day 0 is the due date, so days overdue is just today's day number."""
        return self.day

    @property
    def contact_count(self) -> int:
        return len(self.contact_days)

    @property
    def last_contact_day(self) -> Optional[int]:
        return self.contact_days[-1] if self.contact_days else None

    @property
    def days_since_last_contact(self) -> Optional[int]:
        last = self.last_contact_day
        return None if last is None else self.day - last


def redact(state: InvoiceState, day: int, actions_taken: tuple) -> ObservedState:
    """Strip an InvoiceState down to what a policy may legitimately see.

    This function is the rail. If a policy ever needs something it cannot get
    from an ObservedState, the honest fix is to argue that a real collections
    team would know it and add the field here — not to reach past this function.
    """
    return ObservedState(
        invoice_id=state.invoice_id,
        amount=state.amount,
        day=day,
        status=state.status,
        actions_taken=actions_taken,
        contact_days=state.contact_days,
        routed_day=state.routed_day,
        legal_day=state.legal_day,
        plan_offered_day=state.plan_offered_day,
        cost_so_far=state.total_cost,
        ptp_date=state.ptp_date,
        ptp_broken=state.ptp_broken,
        opted_out=state.opted_out,
    )


# ---------------------------------------------------------------------------
# The record of one invoice's whole 90-day life under one policy
# ---------------------------------------------------------------------------


@dataclass
class InvoiceRun:
    """Everything that happened to one invoice under one policy."""

    invoice_id: str
    latent_cause: str                 # scoring only — never shown to a policy
    amount: float
    final_state: InvoiceState
    decisions: list                   # one dict per day the policy acted
    suppressed_contacts: int = 0      # contacts the opt-out rail blocked

    @property
    def paid(self) -> bool:
        return self.final_state.status == PAID

    @property
    def cash(self) -> float:
        return self.final_state.paid_amount

    @property
    def cost(self) -> float:
        return self.final_state.total_cost

    @property
    def contacts(self) -> int:
        return self.final_state.contact_count

    @property
    def paid_day(self) -> Optional[int]:
        return self.final_state.paid_day


# ---------------------------------------------------------------------------
# Running one invoice
# ---------------------------------------------------------------------------


def run_invoice(policy, row, horizon: int = cfg.HORIZON_DAYS) -> InvoiceRun:
    """Walk a single invoice from day 0 to the horizon under one policy.

    The loop is deliberately boring: ask, rail, step, record. All the
    intelligence lives in the policy and all the physics lives in the world
    model.
    """
    state = simulator.to_state(row)
    view = simulator.observable_view(row)

    decisions: list = []
    actions_taken: tuple = ()
    suppressed = 0

    for day in range(horizon + 1):
        if state.is_terminal:
            break

        observed = redact(state, day, actions_taken)
        action, reason = policy.decide(view, observed, day)

        if action not in cfg.ACTIONS:
            raise ValueError(
                f"{policy.name} returned unknown action {action!r} for "
                f"{state.invoice_id} on day {day}"
            )

        # --- WORLD RAIL: you cannot contact someone who told you to stop ----
        rail = None
        if action in cfg.CONTACT_ACTIONS and state.opted_out:
            rail = f"opt_out_suppressed:{action}"
            action = cfg.WAIT
            suppressed += 1

        outcome: Outcome = step(state, action, day)
        state = outcome.state

        # Log the days that matter: anything we actually did, anything the
        # world rail blocked, and any day money arrived. Cash often lands on a
        # WAIT day — a log that omits the payment is a log that hides the
        # outcome, so WAIT days are silent EXCEPT when they pay.
        if action != cfg.WAIT or rail is not None or outcome.cash_collected:
            if action != cfg.WAIT:
                actions_taken = actions_taken + ((day, action),)
            decisions.append({
                "invoice_id": state.invoice_id,
                "day": day,
                "action": action,
                "reason": reason,
                "world_rail": rail,
                "cost": outcome.cost,
                "cash": outcome.cash_collected,
                "events": list(outcome.events),
            })

    # PRD C2.7 — the 90-day clock runs out, whatever is left is a write-off.
    state = finalize_at_horizon(state)

    return InvoiceRun(
        invoice_id=str(row["invoice_id"]),
        latent_cause=str(row["latent_cause"]),
        amount=float(row["amount"]),
        final_state=state,
        decisions=decisions,
        suppressed_contacts=suppressed,
    )


# ---------------------------------------------------------------------------
# Running the whole book
# ---------------------------------------------------------------------------


@dataclass
class RunResult:
    """One policy's complete run over the whole invoice book."""

    policy_name: str
    runs: list                        # list[InvoiceRun]
    overall: dict                     # the headline metric block
    by_cause: dict                    # cause -> the same metric block

    @property
    def decisions(self) -> list:
        """Every decision from every invoice, flattened. Phase 4 audit feed."""
        return [d for r in self.runs for d in r.decisions]


def run_policy(policy, df: pd.DataFrame, horizon: int = cfg.HORIZON_DAYS) -> RunResult:
    """Score one policy over every invoice in `df`.

    `policy` is any object with a `name` and a
    `decide(view, observed, day) -> (action, reason)` method.
    """
    runs = [run_invoice(policy, row, horizon) for _, row in df.iterrows()]

    by_cause = {}
    for cause in cfg.CAUSES:
        subset = [r for r in runs if r.latent_cause == cause]
        by_cause[cause] = _score(subset)

    return RunResult(
        policy_name=policy.name,
        runs=runs,
        overall=_score(runs),
        by_cause=by_cause,
    )


def _score(runs: list) -> dict:
    """Turn a list of finished invoices into the metric block.

    Two recovery rates are reported on purpose, because they answer different
    questions and CHRONIC only ever pays 40%:
      - `recovery_rate_count`  what share of INVOICES got paid at all
      - `recovery_rate_value`  what share of the RUPEES billed came back

    `mean_days_to_cash` is measured over paid invoices only, so read it beside
    the recovery rate: a policy that collects nothing but the three easiest
    invoices posts a wonderful DSO.
    """
    n = len(runs)
    if n == 0:
        return {
            "invoices": 0, "billed": 0.0, "recovered": 0.0,
            "paid_count": 0, "recovery_rate_count": 0.0, "recovery_rate_value": 0.0,
            "contacts": 0, "suppressed_contacts": 0, "cost": 0.0,
            "net_recovery": 0.0, "mean_days_to_cash": None, "written_off": 0,
        }

    billed = sum(r.amount for r in runs)
    recovered = sum(r.cash for r in runs)
    cost = sum(r.cost for r in runs)
    paid = [r for r in runs if r.paid]
    days = [r.paid_day for r in paid if r.paid_day is not None]

    return {
        "invoices": n,
        "billed": billed,
        "recovered": recovered,
        "paid_count": len(paid),
        "recovery_rate_count": len(paid) / n,
        "recovery_rate_value": recovered / billed if billed else 0.0,
        "contacts": sum(r.contacts for r in runs),
        "suppressed_contacts": sum(r.suppressed_contacts for r in runs),
        "cost": cost,
        "net_recovery": recovered - cost,
        "mean_days_to_cash": (sum(days) / len(days)) if days else None,
        "written_off": sum(1 for r in runs if r.final_state.status == WRITTEN_OFF),
    }


# ---------------------------------------------------------------------------
# Printing
# ---------------------------------------------------------------------------


def rs(x: float) -> str:
    """Format rupees with Indian-style grouping, no paise."""
    n = int(round(x))
    sign = "-" if n < 0 else ""
    s = str(abs(n))
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        s = ",".join(parts) + "," + tail
    return f"{sign}Rs {s}"


def print_result(result: RunResult) -> None:
    """Print the full metric block — the Phase 2 gate output."""
    o = result.overall
    dso = o["mean_days_to_cash"]

    print()
    print("=" * 76)
    print(f"POLICY: {result.policy_name}")
    print("=" * 76)
    print(f"  Invoices                {o['invoices']:>16,}")
    print(f"  Total billed            {rs(o['billed']):>16}")
    print()
    print(f"  Recovered               {rs(o['recovered']):>16}")
    print(f"  Recovery rate (count)   {o['recovery_rate_count']:>15.1%}   "
          f"{o['paid_count']} of {o['invoices']} invoices paid")
    print(f"  Recovery rate (value)   {o['recovery_rate_value']:>15.1%}   "
          f"share of rupees billed")
    print()
    print(f"  Contacts sent           {o['contacts']:>16,}")
    print(f"  Contacts suppressed     {o['suppressed_contacts']:>16,}   "
          f"blocked by customer opt-out")
    print(f"  Chasing cost            {rs(o['cost']):>16}")
    print()
    print(f"  NET RECOVERY            {rs(o['net_recovery']):>16}   recovered - cost")
    print(f"  Mean days to cash       "
          f"{(f'{dso:.1f}' if dso is not None else 'n/a'):>16}   over paid invoices only")
    print(f"  Written off             {o['written_off']:>16,}")
    print()
    print("-" * 76)
    print("PER-CAUSE BREAKDOWN  (the hidden cause the policy could not see)")
    print("-" * 76)
    print(f"  {'cause':<12} {'n':>4} {'recovered':>14} {'rate':>6} "
          f"{'cont':>5} {'cost':>11} {'net':>14} {'dso':>5}")
    print("  " + "-" * 72)
    for cause in cfg.CAUSES:
        c = result.by_cause[cause]
        cdso = c["mean_days_to_cash"]
        print(f"  {cause:<12} {c['invoices']:>4} {rs(c['recovered']):>14} "
              f"{c['recovery_rate_count']:>6.0%} {c['contacts']:>5} "
              f"{rs(c['cost']):>11} {rs(c['net_recovery']):>14} "
              f"{(f'{cdso:.0f}' if cdso is not None else '-'):>5}")
    print()
