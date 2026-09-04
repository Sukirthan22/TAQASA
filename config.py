"""
config.py — every tunable number in the project lives here.

Plain English: this is the single dial-board for the whole simulation. If you
want to change how much a phone call costs, how many invoices are DISPUTE, or
how long a cash-strapped customer takes to find money, you change it here and
nowhere else. No other module is allowed to contain a magic number.

Read this file top to bottom before reading any other file. Everything the
world model does is driven by constants defined below.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# 1. GLOBAL RUN SETTINGS
# ---------------------------------------------------------------------------

# One seed governs the entire project. Two runs with the same seed must produce
# byte-identical output — that is what makes counterfactual comparison honest.
SEED = 42

# How many invoices the simulator generates.
N_INVOICES = 500

# The simulation clock.
#
#   DAY 0 == THE DAY THE INVOICE FALLS DUE.
#
# Every invoice enters the simulation on day 0 as a fresh cohort, so at any
# point in the run `days_overdue == current_day`. This is why the numbers in
# the PRD line up: the baseline nudges on day 7 (= 7 days overdue), the legal
# guardrail needs days_overdue > 60 (reachable on day 61), and CHRONIC legal
# recovery lands on day 85 (inside the horizon).
HORIZON_DAYS = 90

# File locations (relative to repo root).
DATA_DIR = "data"
RESULTS_DIR = "results"
INVOICES_CSV = "data/invoices.csv"
AUDIT_LOG_JSONL = "results/audit_log.jsonl"


# ---------------------------------------------------------------------------
# 2. THE FOUR HIDDEN CAUSES (PRD B2)
# ---------------------------------------------------------------------------

FORGOTTEN = "FORGOTTEN"
CASH_CRUNCH = "CASH_CRUNCH"
DISPUTE = "DISPUTE"
CHRONIC = "CHRONIC"

CAUSES = (FORGOTTEN, CASH_CRUNCH, DISPUTE, CHRONIC)

# Target mix of causes. Gate 1 requires the realised mix to be within +/-3%.
CAUSE_SHARES = {
    FORGOTTEN: 0.35,
    CASH_CRUNCH: 0.25,
    DISPUTE: 0.20,
    CHRONIC: 0.20,
}

# Gate 1 tolerance on the realised cause distribution (absolute, in share).
CAUSE_SHARE_TOLERANCE = 0.03


# ---------------------------------------------------------------------------
# 3. THE ACTION MENU (PRD B4)
# ---------------------------------------------------------------------------

NUDGE_SOFT = "NUDGE_SOFT"
NUDGE_FIRM = "NUDGE_FIRM"
CALL = "CALL"
OFFER_PLAN = "OFFER_PLAN"
ROUTE_DISPUTE = "ROUTE_DISPUTE"
ESCALATE_LEGAL = "ESCALATE_LEGAL"
WAIT = "WAIT"
WRITE_OFF = "WRITE_OFF"

ACTIONS = (
    NUDGE_SOFT,
    NUDGE_FIRM,
    CALL,
    OFFER_PLAN,
    ROUTE_DISPUTE,
    ESCALATE_LEGAL,
    WAIT,
    WRITE_OFF,
)

# Rupee cost of taking each action once. Costs are the reason "correctly doing
# nothing" is a measurable win rather than a free option.
ACTION_COSTS = {
    NUDGE_SOFT: 20,
    NUDGE_FIRM: 20,
    CALL: 200,
    OFFER_PLAN: 200,
    ROUTE_DISPUTE: 500,
    ESCALATE_LEGAL: 2000,
    WAIT: 0,
    WRITE_OFF: 0,
}

# Actions that actually reach the customer. These are what count toward the
# "maximum 4 contacts" guardrail and toward "the first contact of any kind"
# that wakes up a FORGOTTEN invoice.
#
# ROUTE_DISPUTE is deliberately NOT a contact: it hands the invoice to your own
# internal dispute desk. It costs money but it does not spend contact budget
# and it does not annoy the customer.
CONTACT_ACTIONS = frozenset({NUDGE_SOFT, NUDGE_FIRM, CALL, OFFER_PLAN, ESCALATE_LEGAL})

# Actions that read to the customer as "pay me" chasing. These are what add the
# +3 day annoyance penalty on a DISPUTE invoice when sent before routing.
REMINDER_ACTIONS = frozenset({NUDGE_SOFT, NUDGE_FIRM, CALL})

# Terminal actions — nothing may follow them.
TERMINAL_ACTIONS = frozenset({WRITE_OFF})


# ---------------------------------------------------------------------------
# 4. HOW EACH CAUSE ACTUALLY PAYS (PRD B2 — the world model's rulebook)
# ---------------------------------------------------------------------------

# --- FORGOTTEN -------------------------------------------------------------
# Pays 2 days after the FIRST contact of any kind. Extra contacts do nothing.
FORGOTTEN_PAY_LAG_AFTER_FIRST_CONTACT = 2
# If nobody ever contacts them, it surfaces on its own eventually.
FORGOTTEN_UNCONTACTED_PAY_DAY = 75

# --- CASH_CRUNCH -----------------------------------------------------------
# A hidden liquidity_day, drawn uniformly in this inclusive range, before which
# the customer physically cannot pay. Contacts before it achieve nothing.
CASH_CRUNCH_LIQUIDITY_DAY_MIN = 40
CASH_CRUNCH_LIQUIDITY_DAY_MAX = 70
# Once money exists, a contact converts it into cash 2 days later.
CASH_CRUNCH_PAY_LAG_AFTER_CONTACT = 2
# OFFER_PLAN splits the bill into instalments, pulling the effective liquidity
# day forward by this many days (but never earlier than the day you offered it).
CASH_CRUNCH_PLAN_PULL_FORWARD_DAYS = 10
#
# INTERPRETATION NOTE (read this before defending the model on a panel):
# PRD B2 says CASH_CRUNCH "pays then, or 2 days after the first contact
# following that day". Read literally those two branches collide — self-payment
# on the liquidity day would always beat a later contact, which would make
# contacting a cash-crunch customer pointless and would delete lift source B5.3
# entirely. The intended mechanism is clearly: contacts before the liquidity
# day are wasted, a contact on or after it converts, and an invoice nobody ever
# contacts drifts and pays late on its own. This constant is that drift.
CASH_CRUNCH_UNCONTACTED_PAY_LAG = 15

# --- DISPUTE ---------------------------------------------------------------
# Reminders never resolve a dispute. Only ROUTE_DISPUTE does.
DISPUTE_PAY_LAG_AFTER_ROUTING = 10
# Every reminder sent before routing pushes resolution out — the annoyance tax.
DISPUTE_ANNOYANCE_DAYS_PER_REMINDER = 3

# --- CHRONIC ---------------------------------------------------------------
# Never pays. One exception: a formal legal notice on a large invoice.
CHRONIC_LEGAL_MIN_AMOUNT = 200_000
CHRONIC_LEGAL_RECOVERY_FRACTION = 0.40
CHRONIC_LEGAL_PAY_DAY = 85


# ---------------------------------------------------------------------------
# 5. PROMISE-TO-PAY (what CALL buys you)
# ---------------------------------------------------------------------------
#
# PRD B4 prices CALL at Rs 200 but B2 never says what it does. Locked decision:
# a CALL behaves like any other contact for payment purposes AND may extract a
# promise-to-pay date from the customer.
#
# Critically, a PTP does NOT change when the invoice pays. Payment stays
# governed entirely by the four cause rules above. A PTP is a *behavioural
# signal* that trips stopping rule C2.3 (halt chasing until the promised date
# passes). That means a CHRONIC customer who promises and never pays is a
# genuine trap the guardrail walks into — which is exactly the kind of honest
# failure the report is supposed to surface.

PTP_PROBABILITY_BY_CAUSE = {
    FORGOTTEN: 0.70,     # they meant to pay, they just forgot
    CASH_CRUNCH: 0.80,   # eager to commit to a date they can actually make
    DISPUTE: 0.15,       # won't promise anything while the amount is contested
    CHRONIC: 0.85,       # promises freely, honours nothing — classic stalling
}

# For FORGOTTEN / DISPUTE / CHRONIC the promised date is this many days out.
PTP_DELAY_DAYS_MIN = 7
PTP_DELAY_DAYS_MAX = 21
# A CASH_CRUNCH customer promises around the day money actually arrives.
PTP_CASH_CRUNCH_MIN_DELAY = 3
PTP_CASH_CRUNCH_MAX_DELAY = 30


# ---------------------------------------------------------------------------
# 6. CUSTOMER OPT-OUT (what makes stopping rule C2.4 real)
# ---------------------------------------------------------------------------
#
# Guardrail C2.4 halts chasing permanently on customer opt-out. That guardrail
# can only fire if the world is capable of producing an opt-out, so the
# simulator pre-draws, per invoice, the contact count at which this customer
# tells you to stop. 0 means they never opt out.

OPTOUT_PROBABILITY_BY_CAUSE = {
    FORGOTTEN: 0.05,
    CASH_CRUNCH: 0.08,
    DISPUTE: 0.25,   # being spammed while you are disputing is what triggers it
    CHRONIC: 0.18,
}
OPTOUT_AFTER_CONTACTS_CHOICES = (2, 3, 4)


# ---------------------------------------------------------------------------
# 7. INVOICE AMOUNTS
# ---------------------------------------------------------------------------

# Lognormal, in INR. Median ~= exp(AMOUNT_LOG_MEAN).
AMOUNT_LOG_MEAN = 11.695  # exp(11.695) ~= Rs 1,20,000
AMOUNT_LOG_SIGMA = 0.90
AMOUNT_MIN = 5_000
AMOUNT_MAX = 5_000_000
AMOUNT_ROUND_TO = 100

# Cosmetic only: due dates are spread across a quarter so the CSV looks like a
# real ledger. The simulation clock is relative to each invoice's own due date,
# so the calendar date never affects any decision or payment.
DUE_DATE_START = "2026-04-01"
DUE_DATE_SPREAD_DAYS = 90


# ---------------------------------------------------------------------------
# 8. OBSERVABLE CLUE GENERATION (PRD B3)
# ---------------------------------------------------------------------------
#
# These parameters make the clues CORRELATE with the hidden cause without ever
# determining it. Per cause, one entry per observable feature.

CLUE_PARAMS = {
    FORGOTTEN: {
        "historic_dso_mean": 42, "historic_dso_sd": 10,
        "prior_disputes_lambda": 0.20,
        "prior_writeoffs_lambda": 0.10,
        "po_mismatch_p": 0.05,
        "partial_delivery_p": 0.04,
        "pays_after_dom_min": 1, "pays_after_dom_max": 15,
        "email_opened_p": 0.75,
        "email_replied_given_opened_p": 0.30,
    },
    CASH_CRUNCH: {
        "historic_dso_mean": 62, "historic_dso_sd": 12,
        "prior_disputes_lambda": 0.30,
        "prior_writeoffs_lambda": 0.25,
        "po_mismatch_p": 0.06,
        "partial_delivery_p": 0.05,
        "pays_after_dom_min": 20, "pays_after_dom_max": 28,
        "email_opened_p": 0.80,
        "email_replied_given_opened_p": 0.55,
    },
    DISPUTE: {
        "historic_dso_mean": 55, "historic_dso_sd": 12,
        "prior_disputes_lambda": 2.40,
        "prior_writeoffs_lambda": 0.30,
        "po_mismatch_p": 0.55,
        "partial_delivery_p": 0.45,
        "pays_after_dom_min": 1, "pays_after_dom_max": 18,
        "email_opened_p": 0.85,
        "email_replied_given_opened_p": 0.75,
    },
    CHRONIC: {
        "historic_dso_mean": 88, "historic_dso_sd": 15,
        "prior_disputes_lambda": 0.80,
        "prior_writeoffs_lambda": 1.90,
        "po_mismatch_p": 0.10,
        "partial_delivery_p": 0.08,
        "pays_after_dom_min": 1, "pays_after_dom_max": 28,
        "email_opened_p": 0.15,   # total silence is the CHRONIC tell
        "email_replied_given_opened_p": 0.05,
    },
}

HISTORIC_DSO_MIN = 15
HISTORIC_DSO_MAX = 120
PRIOR_DISPUTES_MAX = 8
PRIOR_WRITEOFFS_MAX = 6

# MSME status affects legal escalation rights, not payment behaviour, so it is
# generated independently of the hidden cause.
IS_MSME_SUPPLIER_P = 0.45

# THE NOISE KNOB (PRD B3). This share of invoices has its observable clues
# generated from a DIFFERENT cause's parameters than its true one. Without this
# the classifier scores 100% and no panelist believes the project.
CLUE_NOISE_RATE = 0.10


# ---------------------------------------------------------------------------
# 9. STOPPING RULES / GUARDRAIL THRESHOLDS (PRD C2)
# ---------------------------------------------------------------------------
# Defined here in Phase 1 so the numbers live in one place; the guardrail
# wrapper that enforces them is built in Phase 4.

MAX_CONTACTS_PER_INVOICE = 4
MIN_DAYS_BETWEEN_CONTACTS = 2          # "minimum 48 hours"
LEGAL_MIN_AMOUNT = 200_000             # ESCALATE_LEGAL requires amount >
LEGAL_MIN_DAYS_OVERDUE = 60            # ...AND days_overdue >


# ---------------------------------------------------------------------------
# 10. COLUMN CONTRACTS — what the agent may and may not look at
# ---------------------------------------------------------------------------
#
# This is the most important safety rail in the project. Anything in
# HIDDEN_COLUMNS is ground truth owned by the world model. If a policy or a
# classifier ever reads one of these, the whole result is worthless. Phases 3
# and 4 select features by OBSERVABLE_COLUMNS only.

OBSERVABLE_COLUMNS = (
    "invoice_id",
    "customer_id",
    "amount",
    "due_date",
    "customer_historic_dso",
    "customer_prior_disputes",
    "customer_prior_writeoffs",
    "po_mismatch_flag",
    "partial_delivery_flag",
    "customer_pays_after_day_of_month",
    "email_opened",
    "email_replied",
    "is_msme_supplier",
)

# Features fed to the Phase 3 classifier (observables minus identifiers and the
# cosmetic due date).
FEATURE_COLUMNS = (
    "amount",
    "customer_historic_dso",
    "customer_prior_disputes",
    "customer_prior_writeoffs",
    "po_mismatch_flag",
    "partial_delivery_flag",
    "customer_pays_after_day_of_month",
    "email_opened",
    "email_replied",
    "is_msme_supplier",
)

HIDDEN_COLUMNS = (
    "latent_cause",
    "hidden_liquidity_day",
    "hidden_ptp_will_promise",
    "hidden_ptp_delay_days",
    "hidden_optout_after_contacts",
    "hidden_clue_noise",
)

ALL_COLUMNS = OBSERVABLE_COLUMNS + HIDDEN_COLUMNS


# ---------------------------------------------------------------------------
# 11. CAUSE INFERENCE (PRD Phase 3)
# ---------------------------------------------------------------------------
#
# Two arms, both scored on the SAME held-out split so the comparison is fair:
#   Rules arm    explicit if/else over the clues. No training. Always works.
#   Learned arm  a depth-capped decision tree, so it can be printed and read.

# The split. 350 train / 150 test, stratified so every cause keeps its share.
TRAIN_SIZE = 350
TEST_SIZE = 150

# Depth 4 is a hard cap, not a hyperparameter to tune. A tree deeper than this
# cannot be printed in a README and read by a human, and an unreadable model
# defeats the point of using a tree instead of something stronger.
TREE_MAX_DEPTH = 4

# --- Rules-arm thresholds --------------------------------------------------
# Every number below is read off the CLUE_PARAMS table in section 8. They are
# stated here rather than inline so a panelist can check the rules against the
# generator without reading any code.

# CHRONIC tell: the customer never even opens the email, AND has a history of
# leaving suppliers unpaid or takes far longer than anyone else to pay.
RULE_CHRONIC_MIN_WRITEOFFS = 1
RULE_CHRONIC_MIN_DSO = 75

# DISPUTE tell: a paperwork problem on this invoice, or a customer who argues
# about invoices as a matter of habit.
RULE_DISPUTE_MIN_PRIOR_DISPUTES = 2

# CASH_CRUNCH tell: pays only late in the month, when their own money lands.
RULE_CASH_MIN_DAY_OF_MONTH = 20
RULE_CASH_MIN_DSO = 60

# Confidence the rules arm reports when each branch fires. These are honest
# self-assessments, not probabilities: the CHRONIC branch needs two independent
# signals to agree so it is trusted more than the catch-all FORGOTTEN branch,
# which fires simply because nothing else did.
RULE_CONFIDENCE = {
    CHRONIC: 0.80,
    DISPUTE: 0.70,
    CASH_CRUNCH: 0.75,
    FORGOTTEN: 0.55,
}


# ---------------------------------------------------------------------------
# 12. THE AGENT POLICY (PRD Phase 4 / B5)
# ---------------------------------------------------------------------------
#
# One block per inferred cause. Read this next to section 4 (how each cause
# actually pays) — every number below is an answer to a rule in that section.

# --- FORGOTTEN -------------------------------------------------------------
# They pay 2 days after the FIRST contact, and extra contacts do nothing. So
# there is exactly one decision to make: how early to send the one nudge. Day 3
# rather than the baseline's day 7 simply because earlier is strictly better
# and there is no cost to being prompt beyond the Rs 20.
AGENT_FORGOTTEN_NUDGE_DAY = 3

# --- CASH_CRUNCH -----------------------------------------------------------
# The agent CANNOT see liquidity_day. It knows only that the customer looks
# cash-constrained, not when their money lands — `customer_pays_after_day_of_
# month` says they pay late in the month, but it is drawn independently of
# liquidity_day in the simulator, so it carries no timing information at all.
#
# So the agent aims at the plausible range (40-70) instead. Two contacts:
# one in the middle of the window, one at the far end. A contact before the
# money arrives is wasted, so the first shot deliberately does not come early.
AGENT_CASH_CRUNCH_FIRST_CONTACT_DAY = 55
AGENT_CASH_CRUNCH_SECOND_CONTACT_DAY = 70

# On a large invoice the first contact is an instalment offer rather than a
# nudge. It costs Rs 200 instead of Rs 20, but it pulls the liquidity day
# forward 10 days, which on a big invoice buys back far more than it costs.
AGENT_OFFER_PLAN_MIN_AMOUNT = 200_000

# --- CHRONIC ---------------------------------------------------------------
# Legal notice is only permitted once the invoice is more than 60 days overdue
# (guardrail C2.5), so the earliest legal day is 61. Any earlier attempt is
# vetoed by the guardrail rather than silently rescheduled.
AGENT_LEGAL_DAY = LEGAL_MIN_DAYS_OVERDUE + 1
