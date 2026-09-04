"""
world_model.py — the rulebook that decides whether an invoice gets paid.

Plain English: this is the universe. You hand it an invoice, an action you took
today, and today's date, and it tells you what happened — did any money arrive,
what did the action cost, and what is the invoice's new state. It is the only
module allowed to look at the hidden `latent_cause`. Nothing else in the
project may.

The whole project rests on one property of this file:

    IT IS A PURE FUNCTION.

`step(state, action, day)` reads nothing global, mutates nothing, and rolls no
dice. Every random draw a customer will ever need — when their money arrives,
whether they will promise to pay, when they will opt out — was made once at
generation time and is carried on the invoice itself. That is what lets us run
the baseline over an invoice, rewind time, run the agent over the SAME invoice,
and legitimately compare the two. Without purity there is no counterfactual and
the headline metric is meaningless.

How to read the payment logic: each cause has a `_pay_day_*` function that
answers one question — "given everything that has been done to this invoice so
far, on what day does the money arrive?" It returns None for "never, on current
evidence". The answer can move: sending another reminder to a DISPUTE invoice
pushes its pay day further out. After the action is applied, `step` recomputes
the pay day and, if today has reached it, collects the cash.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Optional

import config as cfg


# ---------------------------------------------------------------------------
# Status values an invoice can be in
# ---------------------------------------------------------------------------

OPEN = "OPEN"
PAID = "PAID"
WRITTEN_OFF = "WRITTEN_OFF"

TERMINAL_STATUSES = frozenset({PAID, WRITTEN_OFF})


# ---------------------------------------------------------------------------
# The state of one invoice at one moment in time
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class InvoiceState:
    """Everything the world knows about one invoice right now.

    Frozen on purpose. `step` never edits a state in place; it builds a new one.
    That makes it impossible to accidentally leak state from the baseline run
    into the agent run.

    The first block of fields is fixed at generation and never changes. The
    second block is history that accumulates as the run proceeds.
    """

    # --- fixed at generation ------------------------------------------------
    invoice_id: str
    amount: float
    latent_cause: str                       # HIDDEN from every policy
    liquidity_day: Optional[int]            # HIDDEN, CASH_CRUNCH only
    ptp_will_promise: bool                  # HIDDEN, pre-drawn for purity
    ptp_delay_days: int                     # HIDDEN, pre-drawn for purity
    optout_after_contacts: int              # HIDDEN, 0 == never opts out

    # --- accumulates during the run ----------------------------------------
    status: str = OPEN
    contact_days: tuple = ()                # days a CONTACT_ACTION was taken
    reminders_before_routing: int = 0       # DISPUTE annoyance counter
    routed_day: Optional[int] = None        # day ROUTE_DISPUTE was sent
    legal_day: Optional[int] = None         # day ESCALATE_LEGAL was sent
    plan_offered_day: Optional[int] = None  # day OFFER_PLAN was sent
    ptp_date: Optional[int] = None          # promised pay day, if any
    ptp_broken: bool = False                # promised date passed, no money
    opted_out: bool = False
    paid_day: Optional[int] = None
    paid_amount: float = 0.0
    total_cost: float = 0.0                 # rupees spent chasing so far

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES

    @property
    def contact_count(self) -> int:
        return len(self.contact_days)

    @property
    def last_contact_day(self) -> Optional[int]:
        return self.contact_days[-1] if self.contact_days else None


@dataclass
class Outcome:
    """What `step` reports back for one day of one invoice."""

    state: InvoiceState
    cash_collected: float   # rupees that arrived TODAY (0.0 on most days)
    cost: float             # rupees the action cost TODAY
    events: list = field(default_factory=list)  # human-readable, for the audit log

    @property
    def is_terminal(self) -> bool:
        return self.state.is_terminal


# ---------------------------------------------------------------------------
# Per-cause payment rules (PRD B2)
# ---------------------------------------------------------------------------


def _pay_day_forgotten(s: InvoiceState) -> Optional[int]:
    """They just forgot. One contact fixes it; more contacts add nothing.

    Pays 2 days after the FIRST contact of any kind. If nobody ever gets in
    touch, it eventually surfaces by itself on day 75.
    """
    if s.contact_days:
        return s.contact_days[0] + cfg.FORGOTTEN_PAY_LAG_AFTER_FIRST_CONTACT
    return cfg.FORGOTTEN_UNCONTACTED_PAY_DAY


def _pay_day_cash_crunch(s: InvoiceState) -> Optional[int]:
    """They want to pay but have no money until `liquidity_day`.

    Contacts before that day are pure waste — this is where the baseline burns
    its whole contact budget. A contact on or after it converts to cash in 2
    days. OFFER_PLAN pulls the liquidity day forward by up to 10 days, but
    never earlier than the day the plan was actually offered.
    """
    liquidity = s.liquidity_day
    if liquidity is None:
        raise ValueError(f"{s.invoice_id}: CASH_CRUNCH invoice has no liquidity_day")

    effective = liquidity
    if s.plan_offered_day is not None:
        pulled = liquidity - cfg.CASH_CRUNCH_PLAN_PULL_FORWARD_DAYS
        effective = max(pulled, s.plan_offered_day)

    for day in s.contact_days:
        if day >= effective:
            return day + cfg.CASH_CRUNCH_PAY_LAG_AFTER_CONTACT

    # Never usefully contacted: the invoice drifts and they get to it late.
    return effective + cfg.CASH_CRUNCH_UNCONTACTED_PAY_LAG


def _pay_day_dispute(s: InvoiceState) -> Optional[int]:
    """They think the invoice is wrong. Reminders cannot fix that — ever.

    Only ROUTE_DISPUTE resolves it, 10 days later. Every reminder fired before
    routing adds 3 days to that resolution: the annoyance tax the baseline pays
    without ever knowing it.
    """
    if s.routed_day is None:
        return None
    annoyance = cfg.DISPUTE_ANNOYANCE_DAYS_PER_REMINDER * s.reminders_before_routing
    return s.routed_day + cfg.DISPUTE_PAY_LAG_AFTER_ROUTING + annoyance


def _pay_day_chronic(s: InvoiceState) -> Optional[int]:
    """Insolvent or systematically stiffing suppliers. Never pays.

    The one exception: a formal legal notice on an invoice above Rs 2,00,000
    claws back 40% on day 85. Sent after day 85 it recovers nothing inside the
    90-day horizon — escalating late is the same as not escalating.
    """
    if s.legal_day is None:
        return None
    if s.amount <= cfg.CHRONIC_LEGAL_MIN_AMOUNT:
        return None
    if s.legal_day > cfg.CHRONIC_LEGAL_PAY_DAY:
        return None
    return cfg.CHRONIC_LEGAL_PAY_DAY


_PAY_DAY_RULES = {
    cfg.FORGOTTEN: _pay_day_forgotten,
    cfg.CASH_CRUNCH: _pay_day_cash_crunch,
    cfg.DISPUTE: _pay_day_dispute,
    cfg.CHRONIC: _pay_day_chronic,
}


def pay_day(s: InvoiceState) -> Optional[int]:
    """The day this invoice pays, given everything done to it so far.

    None means "never, on current evidence". The answer is allowed to move as
    more actions are taken — that is the whole point.
    """
    rule = _PAY_DAY_RULES.get(s.latent_cause)
    if rule is None:
        raise ValueError(f"{s.invoice_id}: unknown latent_cause {s.latent_cause!r}")
    return rule(s)


def _recovery_fraction(s: InvoiceState) -> float:
    """What share of the invoice actually lands. Only CHRONIC pays partially."""
    if s.latent_cause == cfg.CHRONIC:
        return cfg.CHRONIC_LEGAL_RECOVERY_FRACTION
    return 1.0


# ---------------------------------------------------------------------------
# THE step FUNCTION
# ---------------------------------------------------------------------------


def step(state: InvoiceState, action: str, day: int) -> Outcome:
    """Advance one invoice by one day.

    Takes the invoice's current state, the action taken today, and today's day
    number. Returns the new state, any cash that arrived, and what it cost.

    Deterministic and side-effect free: same state + same action + same day
    always produces the same Outcome, forever.
    """
    if action not in cfg.ACTIONS:
        raise ValueError(f"{state.invoice_id}: unknown action {action!r}")
    if state.is_terminal:
        raise ValueError(
            f"{state.invoice_id}: cannot act on a {state.status} invoice "
            f"(attempted {action} on day {day}). PAID and WRITE_OFF are terminal."
        )

    events: list = []
    cost = float(cfg.ACTION_COSTS[action])
    new = replace(state, total_cost=state.total_cost + cost)

    # --- 1. Apply the action -----------------------------------------------

    if action == cfg.WRITE_OFF:
        new = replace(new, status=WRITTEN_OFF)
        events.append("written_off")
        return Outcome(state=new, cash_collected=0.0, cost=cost, events=events)

    if action != cfg.WAIT:
        new = _apply_action(new, action, day, events)

    # --- 2. Did the money arrive today? ------------------------------------

    due = pay_day(new)
    if due is not None and day >= due:
        fraction = _recovery_fraction(new)
        cash = round(new.amount * fraction, 2)
        new = replace(new, status=PAID, paid_day=day, paid_amount=cash)
        events.append(f"paid:{cash:.2f}" + ("" if fraction == 1.0 else f":partial_{fraction:.0%}"))
        return Outcome(state=new, cash_collected=cash, cost=cost, events=events)

    # --- 3. Did a promise-to-pay lapse? ------------------------------------
    # A PTP does not cause payment; it only halts chasing. When the promised
    # day passes with no money, the promise is marked broken so the guardrail
    # can release the invoice and the report can count how often each cause
    # lied to us.
    if new.ptp_date is not None and not new.ptp_broken and day > new.ptp_date:
        new = replace(new, ptp_broken=True)
        events.append("ptp_broken")

    return Outcome(state=new, cash_collected=0.0, cost=cost, events=events)


def _apply_action(s: InvoiceState, action: str, day: int, events: list) -> InvoiceState:
    """Record the consequences of a non-WAIT, non-WRITE_OFF action."""

    # Routing to the internal dispute desk. Not a customer contact: it costs
    # money but spends no contact budget and causes no annoyance.
    if action == cfg.ROUTE_DISPUTE:
        if s.routed_day is not None:
            events.append("route_dispute_duplicate_ignored")
            return s
        events.append("routed_to_dispute_desk")
        return replace(s, routed_day=day)

    # Everything else in the menu reaches the customer.
    if action in cfg.CONTACT_ACTIONS:
        s = replace(s, contact_days=s.contact_days + (day,))
        events.append(f"contacted:{action}")

        # A chasing message sent while a dispute is still unrouted costs you
        # three days of goodwill.
        if action in cfg.REMINDER_ACTIONS and s.routed_day is None:
            s = replace(s, reminders_before_routing=s.reminders_before_routing + 1)

        if action == cfg.OFFER_PLAN and s.plan_offered_day is None:
            s = replace(s, plan_offered_day=day)
            events.append("instalment_plan_offered")

        if action == cfg.ESCALATE_LEGAL and s.legal_day is None:
            s = replace(s, legal_day=day)
            events.append("legal_notice_issued")

        # A phone call is the only action that can extract a promise-to-pay.
        if action == cfg.CALL and s.ptp_date is None and s.ptp_will_promise:
            promised = _promised_day(s, day)
            s = replace(s, ptp_date=promised)
            events.append(f"ptp_obtained:day_{promised}")

        # Have we now pushed them far enough that they tell us to stop?
        if s.optout_after_contacts and s.contact_count >= s.optout_after_contacts:
            if not s.opted_out:
                s = replace(s, opted_out=True)
                events.append("customer_opted_out")

        return s

    raise ValueError(f"{s.invoice_id}: action {action!r} has no defined effect")


def _promised_day(s: InvoiceState, day: int) -> int:
    """The date the customer commits to on the phone.

    Deterministic: built from values pre-drawn at generation time. A cash-crunch
    customer names the day their money actually arrives (they know their own
    cash flow). Everyone else names a generic date a couple of weeks out —
    which, for a CHRONIC customer, is a date they have no intention of keeping.
    """
    if s.latent_cause == cfg.CASH_CRUNCH and s.liquidity_day is not None:
        delay = s.liquidity_day - day
        delay = max(cfg.PTP_CASH_CRUNCH_MIN_DELAY, min(cfg.PTP_CASH_CRUNCH_MAX_DELAY, delay))
        return day + delay
    return day + s.ptp_delay_days


# ---------------------------------------------------------------------------
# End-of-horizon handling (PRD C2.7)
# ---------------------------------------------------------------------------


def finalize_at_horizon(state: InvoiceState) -> InvoiceState:
    """Anything still open when the 90-day clock runs out becomes a write-off."""
    if state.is_terminal:
        return state
    return replace(state, status=WRITTEN_OFF)
