# B2B Receivables Chaser — PRD & Build Plan
**Razorpay AI Buildathon 2026 · Track 3: AI Revenue Recovery**
Solo build · Aug 31 – Sept 4, 2026 · Submit Sept 4 (deadline Sept 5 — verify on the official form)

---

## PART A — WHAT THIS IS

### A1. The problem in one paragraph

A business sends invoices to other businesses. Many get paid late or never. Today, chasing them is a dumb fixed schedule: reminder on day 7, another on day 21, phone call on day 45 — the same treatment for everyone. That's wrong, because invoices go unpaid for completely different reasons, and each reason needs a different response. Chasing a customer who is disputing the invoice does nothing except annoy them. Chasing a customer who has gone under wastes money forever. Waiting patiently on a customer who simply forgot leaves cash sitting idle.

### A2. What we are building

An agent that reads each overdue invoice, works out **why** it is unpaid, picks the **right** action for that reason, stops when it should stop, and logs every decision — plus a measurement rig that proves it beats the dumb fixed schedule in rupees.

### A3. The single number this project lives or dies by

> **Net incremental recovery** = (agent's rupees recovered − agent's chasing costs) − (baseline's rupees recovered − baseline's chasing costs), on the identical set of invoices.

**Plain English:** Take 500 invoices. Run the dumb system on them — see how much money it gets back and what it spent chasing. Then rewind time, run the smart agent on the *exact same 500 invoices*, and compare. The gap is the whole submission.

**Why not just "₹X recovered":** Some money comes back regardless of who is chasing it. Reporting gross recovery measures the invoices, not your agent. Razorpay's own track bar says *one cherry-picked match proves nothing.*

**Jargon:** the dumb system is the **baseline** or **control arm**. The gap is the **lift**. Because we can replay the same invoice under different policies, we get true **counterfactual** measurement — impossible with real historical data, which only ever records the one branch that actually happened. That is precisely why we simulate.

---

## PART B — THE WORLD MODEL

*This is the heart of the project. Build this first and everything else follows.*

### B1. Why a simulator instead of a real dataset

Real invoice datasets record what happened once, under someone else's policy. They cannot tell you what *would* have happened if you'd waited three more days, or called instead of emailed. Without that, you cannot score a new decision policy at all. The simulator isn't a shortcut around missing data — it's the only structure that makes the core metric computable.

### B2. The four hidden causes

Every generated invoice carries a `latent_cause` the agent **cannot see**. It must be inferred from observable clues.

| Cause | Share | Plain English | How it actually gets paid |
|---|---|---|---|
| `FORGOTTEN` | 35% | Lost in someone's inbox or stuck awaiting an approval signature | Pays 2 days after the **first** contact of any kind. Extra contacts do nothing. If never contacted, pays on day 75. |
| `CASH_CRUNCH` | 25% | Intends to pay but is temporarily short of cash | Has a hidden `liquidity_day` (40–70 days after due date). Pays then, or 2 days after the first contact *following* that day. Contacts before it achieve nothing. `OFFER_PLAN` pulls payment forward 10 days. |
| `DISPUTE` | 20% | Believes something is wrong — wrong amount, short delivery, PO mismatch | Never pays from reminders. Pays 10 days after `ROUTE_DISPUTE`. Each reminder sent *before* routing adds +3 days to resolution (annoyance). |
| `CHRONIC` | 20% | Insolvent, or systematically stiffing suppliers | Never pays. Sole exception: `ESCALATE_LEGAL` on invoices above ₹2,00,000 recovers 40% on day 85. |

### B3. What the agent can see (observable features)

The cause is hidden; these clues are not. Generate them so they *correlate* with cause but never determine it perfectly — otherwise inference is trivial and the project is fake.

- `amount`, `due_date`, `days_overdue`
- `customer_historic_dso` — how long this customer usually takes
- `customer_prior_disputes`, `customer_prior_writeoffs`
- `po_mismatch_flag`, `partial_delivery_flag` — lean toward DISPUTE
- `customer_pays_after_day_of_month` — leans toward CASH_CRUNCH
- `email_opened`, `email_replied` — total silence leans CHRONIC
- `is_msme_supplier` — affects legal escalation rights, not payment behaviour
- `contact_history` — everything sent so far

**Deliberately add noise:** roughly 10% of invoices should carry a clue pointing at the wrong cause. Without this the agent scores 100% and no panelist believes it.

### B4. The action menu

| Action | Cost | Plain English |
|---|---|---|
| `NUDGE_SOFT` | ₹20 | Polite reminder email |
| `NUDGE_FIRM` | ₹20 | Firm email with statement of account |
| `CALL` | ₹200 | Human picks up the phone |
| `OFFER_PLAN` | ₹200 | Offer to split into instalments |
| `ROUTE_DISPUTE` | ₹500 | Hand to the internal dispute-resolution desk |
| `ESCALATE_LEGAL` | ₹2,000 | Formal notice (MSMED Act route where applicable) |
| `WAIT` | ₹0 | Deliberately do nothing today |
| `WRITE_OFF` | ₹0 | Give up permanently. Terminal. |

**Why costs matter:** without them, the optimal strategy is to spam every action at every invoice. Costs force real judgement and make *correctly doing nothing* a measurable win — which is your false-positive story.

### B5. Where the agent's advantage should come from

Four distinct sources. The final report should break the lift down across all four:

1. **Dispute recovery** — money the baseline never gets at all, because reminders don't resolve disputes.
2. **Chronic cost avoidance** — 3 wasted contacts on every chronic invoice, saved.
3. **Cash-crunch patience** — not burning contacts before the customer physically has money.
4. **Faster forgotten recovery** — nudging on day 3 instead of day 7 lowers DSO.

---

## PART C — REQUIREMENTS

### C1. Must have
- Simulator producing 500 invoices with hidden `latent_cause` and a deterministic, replayable world model
- Baseline policy: fixed ladder, no inference — `NUDGE_SOFT` day 7, `NUDGE_FIRM` day 21, `CALL` day 45, identical for every invoice
- Agent policy: cause inference → action selection → bounded execution
- Both policies scored on the **identical** 500 invoices via one shared harness
- Stopping rules, hardcoded and visible
- Append-only audit log, one row per decision, each with a reason string
- Cause-inference confusion matrix on a held-out split
- Honest "where it fails" section naming at least 3 real failure modes
- Public GitHub repo, one-page architecture diagram, 5-minute video

### C2. Stopping rules (non-negotiable, stated in README)
1. Maximum 4 contacts per invoice, ever
2. Minimum 48 hours between any two contacts
3. Auto-halt on promise-to-pay until the promised date passes
4. Auto-halt permanently on customer opt-out
5. `ESCALATE_LEGAL` requires amount > ₹2,00,000 **and** days_overdue > 60
6. `WRITE_OFF` and `PAID` are terminal — no action after either
7. Horizon is 90 days; anything unresolved becomes a write-off

### C3. Constraints
- **Stack (locked):** Python 3.11, pandas, numpy, scikit-learn (shallow decision tree), matplotlib for two charts. LLM used *only* to draft reminder message text — never for the pay/wait/write-off decision, which stays rule-based and auditable.
- No frontend, no FastAPI, no database — CSV and JSONL on disk
- Everything seeded (`SEED = 42`); two runs must produce identical numbers
- All amounts in INR

### C4. Out of scope
OCR / invoice image parsing · real Razorpay API integration · Hinglish voice recovery · dashboard or web UI · reinforcement learning · multi-currency · deployment or hosting · payment-failure recovery (the earlier direction, dropped)

---

## PART D — PHASE-BY-PHASE BUILD PLAN

*Instructions for the AI coding agent. Each phase has a hard gate. Do not begin a phase until the previous gate passes.*

### Rules for the coding agent
1. Build phases strictly in order. Do not scaffold ahead.
2. Do not add features not in this document. If something seems missing, stop and ask.
3. Every module gets a docstring explaining it in plain English.
4. Print numbers at every phase gate — never claim a gate passed without showing output.
5. No silent fallbacks. If something fails, raise loudly.
6. The harness is built before the agent. Never the reverse.

### Rule for Sukirthan
Do not accept a file you cannot explain line by line. A panel will ask *why the decision tree split on `customer_prior_disputes` first*. Typing can be delegated; understanding cannot. Read the diff after every phase.

---

### PHASE 0 — Scaffold · 30 min
**Plain English:** Build the empty rooms before moving furniture in.

```
receivables-chaser/
├── README.md
├── requirements.txt
├── config.py          # SEED, costs, cause shares, thresholds — all constants
├── src/
│   ├── simulator.py
│   ├── world_model.py
│   ├── policies/
│   │   ├── baseline.py
│   │   └── agent.py
│   ├── inference.py
│   ├── harness.py
│   └── audit.py
├── data/
├── results/
└── run.py             # single entry point
```
Every tunable number lives in `config.py`. No magic numbers anywhere else.

**GATE 0:** repo initialised, first commit pushed, `python run.py --help` runs without error.

---

### PHASE 1 — Simulator & world model · 4 hrs · **TODAY'S GATE**
**Plain English:** Invent 500 fake-but-realistic invoices, each secretly carrying a reason it's unpaid, plus a rulebook saying what makes each one get paid.

Build `simulator.py` (invoices with hidden causes and correlated clues, per B2/B3) and `world_model.py` — a pure function:

```python
def step(invoice, action, day) -> Outcome
```

It must be **deterministic**: same invoice + same action sequence = same result, every time. This is what makes replaying both policies on one invoice legitimate.

**GATE 1 — nothing else counts until this passes:**
- `data/invoices.csv` contains exactly 500 rows
- Cause distribution within ±3% of 35/25/20/20
- You have manually opened it and verified 10 rows make business sense
- A throwaway script proves the world model is deterministic across two runs

---

### PHASE 2 — Harness & baseline · 4 hrs
**Plain English:** Build the scoreboard, then build the dumb opponent — so you know the score to beat before you build anything smart.

`harness.py` runs any policy over all 500 invoices, day 0→90, reporting:
- total ₹ recovered · recovery rate % · total contacts · total ₹ cost
- **net recovery** (recovered − cost) · mean days-to-cash (DSO)
- per-cause breakdown of all of the above

`policies/baseline.py` implements the fixed ladder from C1. No inference, no cleverness. That's the point.

**GATE 2:** `python run.py --policy baseline` prints the full metric block, and the per-cause table shows what you'd expect — DISPUTE and CHRONIC recovering ₹0 while still burning contact cost. If it doesn't, the world model is wrong, not the baseline.

---

### PHASE 3 — Cause inference · 5 hrs
**Plain English:** Teach the agent to be a detective — look at the clues, guess which of the four reasons applies.

`inference.py`, two arms, in this order:
1. **Rules arm** — explicit if/else over the observable clues. Ship first; always works, fully explainable.
2. **Learned arm** — `DecisionTreeClassifier(max_depth=4)` trained on 350 invoices, evaluated on a **held-out** 150. Depth-capped so the tree can be printed in the README and read by a human.

Report accuracy and a 4×4 confusion matrix for both. Keep whichever wins, and say so in the README.

**GATE 3:** confusion matrix printed for both arms on the held-out split; accuracy is **not** 100% (if it is, your clues are too clean — add noise per B3).

---

### PHASE 4 — Agent policy, guardrails, audit · 5 hrs
**Plain English:** Now the agent decides what to *do* about each guess, with hard rules on what it's never allowed to do, writing down every decision and why.

`policies/agent.py` maps inferred cause → action, per B5:
- `FORGOTTEN` → single `NUDGE_SOFT` on day 3, then `WAIT`
- `CASH_CRUNCH` → `WAIT` until predicted liquidity window, then `NUDGE_SOFT`; `OFFER_PLAN` if amount is large
- `DISPUTE` → `ROUTE_DISPUTE` immediately, suppress all reminders
- `CHRONIC` → `WRITE_OFF` immediately, unless amount > ₹2,00,000 and overdue > 60 → `ESCALATE_LEGAL`

Guardrails from C2 live in a wrapper that can **veto any action** regardless of what the policy wants. `audit.py` appends one JSONL row per decision: `{invoice_id, day, inferred_cause, confidence, action, reason, guardrail_triggered}`. Append-only — never rewrite.

**GATE 4:** agent beats baseline on net recovery on the identical batch; `results/audit_log.jsonl` has one row per decision; ≥1 row shows a guardrail vetoing an action.

---

### PHASE 5 — Evaluation & honest failure analysis · 4 hrs
**Plain English:** Write down the scoreboard properly — including everywhere the agent got it wrong, before Razorpay finds those cases themselves.

`run.py --compare` produces `results/report.md`:
- side-by-side baseline vs agent metric table
- **net incremental recovery** in ₹ and %
- lift attributed across the four sources in B5
- contacts saved on CHRONIC, and the rupee value of that restraint
- confusion matrix
- two charts: recovery-by-cause, and cumulative cash collected over 90 days

Then dig for real failures and write **Where It Fails** yourself: misclassified DISPUTE invoices chased anyway, CHRONIC written off that would have paid, cash-crunch timing missed. Name at least three, with root causes.

**GATE 5:** `results/report.md` exists with every number populated; Where It Fails names ≥3 specific failures with root causes.

---

### PHASE 6 — README, architecture, video · 4 hrs
**Plain English:** Package it so a stranger gets it in five minutes.

README order — headline number first, because most readers stop after the first screen:
1. One-line problem statement
2. **The result**, up top: baseline recovered ₹X net, agent ₹Y net, incremental ₹Z (+N%)
3. How to run it, in ≤10 commands
4. Architecture diagram
5. Stopping rules as a numbered list
6. Confusion matrix + the printed decision tree
7. **Where it fails**
8. What I'd build next with real Razorpay data

Architecture diagram (one page): `invoice → observable features → cause inference → action selection → guardrail veto layer → world model → audit log → metrics`.

Video script, 5 minutes hard cap:
- 0:00–0:45 the problem, and why fixed dunning ladders are wrong
- 0:45–1:30 why simulation — the counterfactual argument. *Do not skip this; it's the most intellectually serious 45 seconds of the pitch.*
- 1:30–2:30 architecture walkthrough
- 2:30–3:30 the numbers, baseline vs agent
- 3:30–4:15 one failure handled gracefully — show the guardrail veto in the audit log
- 4:15–5:00 what's next with real data

**GATE 6:** repo public and clean, README headline number visible without scrolling, video under 5:00 and recorded on at least the third take.

---

## PART E — SCHEDULE

| Day | Date | Phases | Hard gate by end of day |
|---|---|---|---|
| 1 | Aug 31 | 0, 1 | 500 invoices, 10 manually verified |
| 2 | Sep 1 | 2 | Baseline metrics printed — the number to beat exists |
| 3 | Sep 2 | 3, 4 | Agent beats baseline on net recovery |
| 4 | Sep 3 | 5 | report.md complete with Where It Fails |
| 5 | Sep 4 | 6 + **SUBMIT** | Form submitted with repo, video, architecture |

**Slip rule:** if a gate is missed, cut scope inside that phase — never push the submit date. Sept 4 is fixed; Sept 5 is a cliff, not a buffer.

---

## PART F — DEFINITION OF DONE

Every item is yes/no answerable by a stranger.

1. `data/invoices.csv` has exactly 500 rows, each with a `latent_cause` from the four defined values
2. World model is deterministic — two runs with `SEED=42` produce byte-identical output
3. Baseline policy runs on all 500 and reports ₹ recovered, recovery %, contacts, cost, net
4. Agent policy runs on the identical 500 and reports the same six metrics
5. README states net incremental recovery in both ₹ and %, above the fold
6. Cause inference is evaluated on a held-out split of ≥150 invoices with a 4×4 confusion matrix, and accuracy is below 100%
7. Stopping rules appear in the README as a numbered list of ≥5 rules
8. `results/audit_log.jsonl` has one row per decision, each with a reason string, and ≥1 row showing a guardrail veto
9. README reports how many CHRONIC invoices were correctly left uncontacted and the ₹ saved
10. "Where it fails" names ≥3 specific failures with root causes
11. One-page architecture diagram is committed and rendered in the README
12. Public repo runs end-to-end from clone in ≤10 commands
13. Video is ≤5:00 and covers problem, counterfactual rationale, architecture, numbers, one handled failure
14. Form submitted with repo link, video link, architecture — by Sept 4

---

## PART G — IF YOU'RE AHEAD (strictly optional)

Only touch these if Phase 5 finished early. None is worth missing a gate for.

1. **LLM cause-inference arm** — a third arm alongside rules and tree, all three compared in one table. Strongest single addition: it shows you can tell where an LLM helps and where it's the wrong tool.
2. **Real invoice structure grafting** — use line-item and amount structures from a public invoice dataset as skeletons for the synthetic records, keeping the simulated lifecycle on top. Kills the "toy data" objection.
3. **Sensitivity analysis** — rerun at seeds 42/43/44 and report the metric range. Shows the lift isn't a lucky seed.
4. **Cost-curve chart** — plot net recovery against contact cost to find the optimal aggression level.

---

## PART H — GLOSSARY

| Term | Plain English |
|---|---|
| **Baseline / control arm** | The dumb fixed-schedule version you must beat |
| **Lift** | How much better the agent is than the baseline |
| **Counterfactual** | What *would* have happened under a different action — only observable in a simulator |
| **Latent cause** | The real reason an invoice is unpaid, hidden from the agent |
| **DSO** | Days Sales Outstanding — average days to get paid |
| **Aging bucket** | Grouping invoices by how overdue (0–30, 31–60, 61–90 days) |
| **Dunning ladder** | The escalating reminder sequence |
| **PTP** | Promise-to-pay — customer commits to a date; you track whether they keep it |
| **Write-off** | Formally giving up on collecting |
| **Confusion matrix** | Grid showing what the agent guessed vs the truth, across all four causes |
| **Held-out test set** | Invoices the model never trained on, used for honest scoring |
| **False positive** | Chasing someone you shouldn't have — costs money and goodwill |
| **Terminal state** | A state you can't leave (PAID, WRITE_OFF) |
| **Append-only log** | A record you add to but never edit — the audit trail |
| **MSMED Act, 2006** | Indian law giving small suppliers interest rights on late payments |
