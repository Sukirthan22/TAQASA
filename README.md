# B2B Receivables Chaser

**Razorpay AI Buildathon 2026 · Track 3: AI Revenue Recovery**

Most receivables teams chase overdue invoices on a fixed ladder: reminder on day 7,
firmer reminder on day 21, phone call on day 45 — the same treatment for every
customer. That is wrong, because invoices go unpaid for completely different
reasons, and each reason needs a different response.

This project builds an agent that works out **why** each invoice is unpaid, picks
the right action for that reason, stops when it should stop, and logs every
decision — plus a measurement rig that proves it beats the fixed ladder in rupees.

> **Headline number goes here once Phase 5 lands.**
> Baseline recovered ₹X net · agent recovered ₹Y net · net incremental recovery ₹Z (+N%).

---

## Build status

| Phase | What it is | State |
|---|---|---|
| 0 | Scaffold | done |
| 1 | Simulator & world model | **done — Gate 1 passed, 34/34 checks** |
| 2 | Harness & baseline policy | not started |
| 3 | Cause inference (rules + decision tree) | not started |
| 4 | Agent policy, guardrails, audit log | not started |
| 5 | Evaluation & failure analysis | not started |
| 6 | README, architecture diagram, video | not started |

## Run it

```bash
pip install -r requirements.txt
```

```bash
python run.py --generate
```

```bash
python run.py --verify
```

---

## Why a simulator and not a real dataset

The single number this project lives or dies by is:

> **Net incremental recovery** = (agent recovered − agent cost) − (baseline recovered − baseline cost), on the identical set of invoices.

Computing that requires running two different policies over the *same* invoice and
comparing. Real invoice datasets cannot do this: they record what happened once,
under someone else's collection policy. They cannot tell you what would have
happened if you had waited three more days, or routed to disputes instead of
emailing. Without that counterfactual branch there is no way to score a new
decision policy at all.

The simulator is not a shortcut around missing data. It is the only structure that
makes the core metric computable.

---

## The world model

Every invoice carries a hidden `latent_cause` the agent never sees. It is inferred
from observable clues. The cause is what secretly decides whether money arrives.

| Cause | Share | How it actually gets paid |
|---|---|---|
| `FORGOTTEN` | 35% | Pays 2 days after the **first** contact of any kind. Extra contacts do nothing. Never contacted → pays day 75. |
| `CASH_CRUNCH` | 25% | Has a hidden liquidity day (40–70). Contacts before it are wasted; a contact on or after it converts in 2 days. `OFFER_PLAN` pulls that day forward 10. |
| `DISPUTE` | 20% | Reminders never work. Pays 10 days after `ROUTE_DISPUTE`. Each reminder sent before routing adds +3 days. |
| `CHRONIC` | 20% | Never pays. Sole exception: `ESCALATE_LEGAL` on invoices above ₹2,00,000 recovers 40% on day 85. |

### The action menu

| Action | Cost | What it does |
|---|---|---|
| `NUDGE_SOFT` | ₹20 | Polite reminder email |
| `NUDGE_FIRM` | ₹20 | Firm email with statement of account |
| `CALL` | ₹200 | Human picks up the phone — may extract a promise-to-pay |
| `OFFER_PLAN` | ₹200 | Offer to split into instalments |
| `ROUTE_DISPUTE` | ₹500 | Hand to the internal dispute desk — not a customer contact |
| `ESCALATE_LEGAL` | ₹2,000 | Formal notice (MSMED Act route where applicable) |
| `WAIT` | ₹0 | Deliberately do nothing today |
| `WRITE_OFF` | ₹0 | Give up permanently. Terminal. |

Costs are what make judgement necessary. Without them the optimal strategy is to
fire every action at every invoice, and *correctly doing nothing* stops being a
measurable win.

### Where the agent's advantage should come from

1. **Dispute recovery** — money the baseline never gets, because reminders don't resolve disputes.
2. **Chronic cost avoidance** — three wasted contacts on every chronic invoice, saved.
3. **Cash-crunch patience** — not burning contacts before the customer physically has money.
4. **Faster forgotten recovery** — nudging on day 3 instead of day 7 lowers DSO.

---

## Design decisions this implementation locked in

The PRD left four things underspecified. Each was resolved deliberately, and each
is a question a panel could reasonably ask.

**1. Day 0 is the due date.** Every invoice enters as a fresh cohort, so at any
point `days_overdue == current_day`. This is what makes the PRD's numbers
consistent with each other: baseline nudges on day 7 (= 7 days overdue), the legal
guardrail's `days_overdue > 60` becomes reachable on day 61, and CHRONIC legal
recovery on day 85 lands inside the 90-day horizon.

**2. `CALL` extracts a promise-to-pay.** The PRD priced `CALL` at ₹200 but never
said what it does. It behaves like any other contact for payment purposes, and
additionally may extract a promised date. Crucially a PTP does **not** cause
payment — payment stays governed entirely by the four cause rules. A PTP is a
behavioural signal that trips stopping rule 3 (halt until the promised date
passes). That makes a CHRONIC customer who promises and never pays a real trap the
guardrail walks into, which is exactly the kind of honest failure Phase 5 should
surface rather than hide.

**3. `email_opened` / `email_replied` describe the original invoice email**, not
the agent's chasing. They are generated once, available at day 0, and usable as
classifier features with no leakage from actions the agent later takes.

**4. The cash-crunch "or" was resolved.** PRD B2 says a cash-crunch invoice "pays
then, or 2 days after the first contact following that day". Read literally those
branches collide — self-payment on the liquidity day would always beat a later
contact, making contact pointless and deleting lift source 3. The implemented
reading: contacts before the liquidity day are wasted, a contact on or after it
converts in 2 days, and an invoice nobody ever contacts drifts and pays
`CASH_CRUNCH_UNCONTACTED_PAY_LAG` (15) days late. That constant is the one number
in the world model not given by the PRD, and it is named and isolated in
`config.py` so its effect can be tested.

**5. `ROUTE_DISPUTE` is not a customer contact.** It hands the invoice to your own
internal dispute desk. It costs ₹500 but spends no contact budget and causes no
annoyance.

---

## Stopping rules

Hardcoded, enforced by a wrapper that can veto any action the policy wants
(built in Phase 4). Thresholds live in `config.py`.

1. Maximum 4 contacts per invoice, ever
2. Minimum 48 hours between any two contacts
3. Auto-halt on promise-to-pay until the promised date passes
4. Auto-halt permanently on customer opt-out
5. `ESCALATE_LEGAL` requires amount > ₹2,00,000 **and** days_overdue > 60
6. `WRITE_OFF` and `PAID` are terminal — no action after either
7. Horizon is 90 days; anything unresolved becomes a write-off

---

## Repo layout

```
├── config.py            every tunable number in the project
├── run.py               single entry point
├── verify_phase1.py     the Gate 1 exam paper
├── src/
│   ├── simulator.py     invents the 500 invoices          (Phase 1, done)
│   ├── world_model.py   the rulebook: does it get paid?   (Phase 1, done)
│   ├── inference.py     guess the hidden cause            (Phase 3)
│   ├── harness.py       the scoreboard                    (Phase 2)
│   ├── audit.py         append-only decision log          (Phase 4)
│   └── policies/
│       ├── baseline.py  the fixed ladder                  (Phase 2)
│       └── agent.py     the smart policy                  (Phase 4)
├── data/                invoices.csv
└── results/             report.md, audit_log.jsonl, charts
```

### The leakage rail

`config.py` splits every column into `OBSERVABLE_COLUMNS` and `HIDDEN_COLUMNS`.
`latent_cause` and everything prefixed `hidden_` is ground truth owned by the world
model. Policies receive their input through `simulator.observable_view()`, so
peeking at the answer requires visibly going around a named function.

### Determinism

`world_model.step(state, action, day)` is a pure function: it reads nothing global,
mutates nothing, and rolls no dice. Every random draw a customer will ever need —
when their money arrives, whether they promise to pay, when they opt out — is made
once at generation time and stamped onto the invoice. That is what lets us run the
baseline over an invoice, rewind, run the agent over the same invoice, and
legitimately compare. `python run.py --verify` proves it by hashing two independent
replays.
