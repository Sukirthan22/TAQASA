# B2B Receivables Chaser — Results

> **Net incremental recovery: Rs 1,67,68,240 (+33.1%)**  
> Baseline recovered **Rs 5,06,36,900** net. The agent recovered **Rs 6,74,05,140** net. Same 500 invoices, same world, same 90 days.

Both policies were replayed over the *identical* invoice book. Because the world model is a pure function with every random draw fixed at generation time, the same invoice can be run under two different policies and the difference attributed to the policy alone. That counterfactual is the reason this project simulates rather than using a historical dataset: real data records only the one branch that actually happened.

## 1. Baseline vs agent

| metric | baseline | agent | change |
|---|---:|---:|---:|
| Recovered | Rs 5,07,14,200 | Rs 6,75,44,080 | +Rs 1,68,29,880 |
| Recovery rate (invoices) | 60.0% | 77.2% | +17.2% |
| Recovery rate (rupees) | 57.6% | 76.7% | +19.1% |
| Contacts sent | 1,129 | 350 | -779 |
| Chasing cost | Rs 77,300 | Rs 1,38,940 | +Rs 61,640 |
| **Net recovery** | **Rs 5,06,36,900** | **Rs 6,74,05,140** | **+Rs 1,67,68,240** |
| Mean days to cash | 33.9 | 35.1 | +1.2 |
| Written off | 200 | 114 | -86 |

The agent sent **779 fewer contacts** and still collected Rs 1,68,29,880 more. It spends more in total (Rs 1,38,940 vs Rs 77,300) because dispute routing and legal notices cost far more per action than an email — that is the point, not a defect.

Mean days-to-cash gets **worse** (33.9 → 35.1 days). That is not a rounding artefact and it is explained in Failure 5.

## 2. Where the lift comes from

Split by the hidden cause, and within each cause into the money side and the cost side. The four net figures add up to the headline exactly.

| hidden cause | n | Δ recovered | Δ cost | **Δ net** | contacts |
|---|---:|---:|---:|---:|---:|
| FORGOTTEN | 175 | -Rs 3,67,500 | +Rs 13,140 | **-Rs 3,80,640** | 175 → 149 |
| CASH_CRUNCH | 125 | -Rs 10,00,100 | -Rs 6,700 | **-Rs 9,93,400** | 371 → 128 |
| DISPUTE | 100 | +Rs 1,46,11,400 | +Rs 22,900 | **+Rs 1,45,88,500** | 288 → 26 |
| CHRONIC | 100 | +Rs 35,86,080 | +Rs 32,300 | **+Rs 35,53,780** | 295 → 47 |
| **total** | 500 | +Rs 1,68,29,880 | +Rs 61,640 | **+Rs 1,67,68,240** | 1129 → 350 |

**Two of the four are negative.** The entire lift comes from DISPUTE and CHRONIC; the agent is actively worse than a dumb ladder on FORGOTTEN and CASH_CRUNCH, because on those two causes the baseline was already near perfect and every misclassification is pure downside.

### The four predicted lift sources, graded

| # | predicted source | verdict | evidence |
|---|---|---|---|
| 1 | Dispute recovery | **confirmed** | Rs 1,46,11,400 the baseline could never collect — reminders do not resolve disputes at any volume |
| 2 | Chronic cost avoidance | **confirmed** | contacts on CHRONIC 295 → 47; 63 of 100 left entirely alone |
| 3 | Cash-crunch patience | **partly** | contacts 371 → 128 and cost Rs 29,200 → Rs 22,500, but net is -Rs 9,93,400 once misclassification is counted |
| 4 | Faster forgotten recovery | **failed in aggregate** | day 9.0 → 5.0 where classification is right, but 9.0 → 19.2 across all FORGOTTEN invoices |

Source 3 needs an honest caveat that the original plan did not anticipate: in this world model **every CASH_CRUNCH invoice eventually pays anyway**, because the uncontacted fallback is liquidity + 15 days and the latest possible liquidity day is 70, so the worst case lands on day 85 — inside the 90-day horizon. Patience can therefore only ever buy cost and timing on this cause, never incremental recovery.

## 3. The value of correctly doing nothing

- **63 of 100** CHRONIC invoices were never contacted once.
- The baseline spent **Rs 14,520** chasing those same invoices and recovered nothing from them.
- Contacts on CHRONIC fell from 295 to 47, and 25 of the agent's remaining actions were legal notices (Rs 50,000) which recovered Rs 35,86,080 at 40% of face value.

This is the false-positive story in rupees: the baseline's cost on CHRONIC is money spent to achieve nothing, and restraint is the only thing that recovers it.

## 4. Cause inference

Held-out split of 150 invoices, stratified, never trained on. Accuracy is deliberately **not** 100%: 18 of 150 held-out invoices carry clues generated from the wrong cause and are unlearnable by construction, putting the ceiling near 88%.

**rules arm — accuracy 73.3%**

| truth \ guess | FORG | CASH | DISP | CHRO | recall |
|---|---:|---:|---:|---:|---:|
| FORGOTTEN | 38 | 2 | 13 | 0 | 72% |
| CASH_CRUNCH | 1 | 24 | 8 | 4 | 65% |
| DISPUTE | 5 | 2 | 21 | 2 | 70% |
| CHRONIC | 0 | 1 | 2 | 27 | 90% |
| **precision** | 86% | 83% | 48% | 82% | |

**tree arm — accuracy 72.0%**

| truth \ guess | FORG | CASH | DISP | CHRO | recall |
|---|---:|---:|---:|---:|---:|
| FORGOTTEN | 35 | 2 | 16 | 0 | 66% |
| CASH_CRUNCH | 1 | 30 | 2 | 4 | 81% |
| DISPUTE | 4 | 0 | 22 | 4 | 73% |
| CHRONIC | 5 | 4 | 0 | 21 | 70% |
| **precision** | 78% | 83% | 55% | 72% | |

**The rules arm ships.** Not because it is more accurate — the two are 2 invoices apart on n=150, which is noise — but because its CHRONIC precision is 82% against 72%. CHRONIC is the only guess this agent acts on irreversibly, so precision there is the only number that maps to money. A second reason: the rules arm is not trained on anything, so all 500 invoices are effectively held out when the agent runs, while a tree-driven agent would be scored partly on its own training data.

Across all 500 invoices the agent guessed: 148 FORGOTTEN, 119 CASH_CRUNCH, 126 DISPUTE, 107 CHRONIC.

## 5. Charts

![Recovery by cause](chart_recovery_by_cause.png)

![Cumulative cash](chart_cumulative_cash.png)

## 6. Where it fails

Five real failures, found by comparing what happened to the same invoice under both policies. Every invoice id below is in `data/invoices.csv`.

### Failure 1 — 14 payable invoices written off on day 0 (Rs 13,67,600 forfeited)

The agent wrongly inferred CHRONIC and issued `WRITE_OFF`, which is terminal. The baseline collected every one of these in full.

| invoice | true cause | amount | baseline collected |
|---|---|---:|---|
| `INV-0012` | CASH_CRUNCH | Rs 1,82,300 | day 47 |
| `INV-0460` | CASH_CRUNCH | Rs 1,25,800 | day 67 |
| `INV-0044` | FORGOTTEN | Rs 1,23,800 | day 9 |

**Root cause.** CHRONIC precision is 82%, so about one write-off in five is wrong — and `WRITE_OFF` is the one action with no recovery path. The error is not the classifier being weak; it is pairing a fallible guess with an irreversible action.

**Known and deliberately unfixed.** Replacing `WRITE_OFF` with `WAIT` on small chronic invoices costs nothing (both are Rs 0, and an unrecovered invoice is written off at the horizon anyway) and would recover Rs 13,67,600 of this back. It was kept as specified rather than quietly optimised away; see the note at `config.AGENT_LEGAL_DAY`.

### Failure 2 — 51 of 126 dispute routings were wrong (Rs 25,500 wasted)

DISPUTE precision is only 48% — the weakest number in the whole system. Worse than the wasted Rs 500 per routing is the *silence* that follows it: the DISPUTE playbook suppresses all reminders, so a misclassified invoice is routed and then deliberately ignored.

**23 FORGOTTEN invoices** were routed this way. They still paid — on day 75 on average, against day 9 under the baseline. A customer who merely needed one email waited 66 extra days because the agent decided they were arguing.

**Root cause.** `po_mismatch_flag` and `partial_delivery_flag` fire on 5% and 4% of FORGOTTEN invoices as a base rate, and the rules arm treats either flag alone as sufficient evidence. With FORGOTTEN the largest class, base-rate contamination swamps the branch.

### Failure 3 — 43 of 87 cash-crunch nudges fired before the money arrived

The agent contacts on day 55, but the hidden liquidity day is drawn anywhere in 40–70. For 43 correctly-classified invoices the money had not landed yet, so the contact achieved nothing (Rs 3,380).

**Root cause.** The agent has no feature that carries timing. `customer_pays_after_day_of_month` says the customer is cash-constrained but is drawn *independently* of `liquidity_day` in the simulator, so it contains literally zero information about when money arrives. The agent is aiming at the middle of a range it cannot see into, and no amount of model improvement fixes that — it is a missing-feature problem, not a learning problem.

### Failure 4 — 25 real disputes never routed (Rs 59,37,100 left uncollected)

The mirror image of Failure 2. DISPUTE recall is 70%, and a dispute that is never routed can never be paid — `ROUTE_DISPUTE` is the only action in the entire menu that resolves one. These invoices were chased or ignored and recovered nothing, exactly as they would have under the baseline.

**Root cause.** The ~10% clue noise puts these invoices' observable features in another cause's distribution entirely. They are unlearnable from the features available, and no threshold change recovers them without making Failure 2 worse — precision and recall on DISPUTE trade directly against each other.

### Failure 5 — the agent's mean days-to-cash is worse than the baseline's

Overall DSO went 33.9 → 35.1 days, and on FORGOTTEN invoices specifically it went 9.0 → 19.2. The plan predicted the opposite.

**Root cause.** Nudging on day 3 instead of day 7 works exactly as designed *when the guess is right*: those invoices pay on day 5.0 against day 9.0. But FORGOTTEN invoices misread as DISPUTE get routed and never contacted, so they drift to day 75. A minority of large delays outweighs a majority of small gains. Two further effects push the same way and are honest rather than regrettable: the agent deliberately waits on CASH_CRUNCH, and it collects CHRONIC money on day 85 that the baseline never collects at all — cash the baseline's DSO never has to average in.

**This is the most important caveat in the report.** Reported alone, DSO says the agent is worse. Net recovery says it is 33% better. Both are true, and a receivables team optimising for DSO would reject this agent.

## 7. Reproducing this

```bash
python run.py --generate        # 500 invoices, SEED=42
python run.py --verify          # Gate 1 checks
python run.py --policy baseline
python run.py --infer
python run.py --policy agent
python run.py --compare        # regenerates this file
```

Every number above is computed from the two runs by `src/report.py`; none is typed in. `SEED = 42`, horizon 90 days, 500 invoices. The world model rolls no dice, so repeated runs are identical.
