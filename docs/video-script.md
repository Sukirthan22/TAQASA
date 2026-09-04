# Video script — 5:00 hard cap

Read it out loud with a timer before recording. If a section runs long, cut words from section 3, never from section 2.

**What to have open, in order:** this README (top of page) · `results/chart_cumulative_cash.png` · `docs/architecture.svg` · the report's lift table · `results/audit_log.jsonl` filtered to a veto row.

---

## 0:00–0:45 — The problem

> Businesses send invoices to other businesses, and a lot of them get paid late or never. Today, chasing them is a dumb fixed schedule: a reminder on day 7, a firmer one on day 21, a phone call on day 45. Every customer gets identical treatment.
>
> That's wrong, and here's why. Invoices go unpaid for completely different reasons. Someone who's disputing your invoice will never pay from a reminder — you're just annoying them. Someone who's gone under will never pay at all — every rupee you spend chasing is burned. Someone who simply forgot needs one email, and you're leaving cash idle for a week waiting for day 7 to arrive.
>
> Same ladder, four completely different problems. So I built an agent that works out *why* an invoice is unpaid, and does the one thing that actually works for that reason.

*(Cut if tight: the last sentence of paragraph two.)*

## 0:45–1:30 — Why simulate — do not skip this

> Before any of the code, the hardest question in this project: how do you *prove* a new chasing policy is better?
>
> You can't do it with real invoice data. Real data records what happened once, under somebody else's policy. It cannot tell you what *would* have happened if you'd waited three more days, or called instead of emailed. And without that, you can't score a new policy at all — you can only describe the one branch that actually happened.
>
> So I built a simulator where the world model is a pure function that rolls no dice. Every random draw a customer will ever need is made once, up front, and frozen onto the invoice. That means I can run the dumb ladder over an invoice, rewind time, and run the smart agent over the *exact same* invoice — and the difference is caused by the policy and nothing else.
>
> That's a genuine counterfactual. It's not a shortcut around missing data — it's the only structure that makes the headline number mean anything.

## 1:30–2:30 — Architecture

*(On screen: `docs/architecture.svg`.)*

> Left to right. An invoice arrives with observable features — amount, how long this customer usually takes, prior disputes, whether they even opened the email.
>
> Those go to cause inference, which guesses one of four hidden causes. It's a rules cascade, and it's right about 73% of the time.
>
> The guess picks a playbook. Forgotten gets one early nudge and then silence. Cash crunch gets patience — you do not chase somebody who physically has no money. Dispute gets routed to the dispute desk immediately with all reminders suppressed. Chronic gets written off, or escalated legally if it's large enough.
>
> Then everything passes through the guardrail veto layer, which can overrule the agent regardless of what it wants — maximum four contacts, 48 hours apart, halt on a promise to pay, halt on opt-out, and a legal notice needs the invoice to be over two lakh and over 60 days overdue.
>
> **Point at the red dashed line.** This is the part I care most about. The invoice's true cause lives below that line and never crosses it. The agent is handed a redacted view of the world — only what a real collections team would actually know. Reading the answer would mean visibly going around a named function.

## 2:30–3:30 — The numbers

*(On screen: the cumulative cash chart, then the lift table.)*

> Same 500 invoices, same 90 days, both policies.
>
> The fixed ladder nets **five crore six lakh**. The agent nets **six crore seventy-four lakh**. Net incremental recovery: **one crore sixty-eight lakh, up 33%** — while sending 779 *fewer* messages.
>
> **Point at the chart.** The agent is ahead on every single day of the horizon.
>
> But the number I actually want to show you is this one. **Switch to the lift table.** I broke the lift down by hidden cause, and two of the four are *negative*. All the gain comes from disputes and chronic non-payers. On forgotten and cash-crunch invoices my agent is genuinely *worse* than the dumb ladder, because the ladder was already near-perfect there and every misclassification is pure downside.
>
> And one more: mean days-to-cash gets *worse*, 33.9 to 35.1 days. That's in the README above the fold, not buried. A team optimising for DSO instead of cash should reject this agent.

## 3:30–4:15 — One failure, handled

*(On screen: a veto row in `results/audit_log.jsonl`.)*

> Every decision is written to an append-only log with the reason. 2,944 rows — and 2,009 of them are the guardrail *refusing* the agent.
>
> Here's one. The agent has decided this invoice is chronic and worth a legal notice. The guardrail says no: `C2.5_legal_not_eligible`, amount two lakh thirty-seven thousand, overdue zero days. It's blocked every day until day 61, then released.
>
> Here's why that matters. My first version had the agent check the 60-day rule *itself* — and the veto layer fired **zero times** across all 500 invoices. It looked fine. It was a guardrail nobody had ever tested. So I removed the duplicate check and let the agent state its intent and the guardrail refuse it. Same outcome, but now the rule lives in exactly one place and every refusal is on the record.

## 4:15–5:00 — What's next with real data

> Three things.
>
> One: keep this harness exactly as it is and replace the simulator's payment rules with a survival model fitted to real settlement times. The counterfactual harness is the reusable asset — the world model is the part that should be learned rather than assumed.
>
> Two: make irreversibility explicit in the decision. My two most expensive failures are the same bug — the agent computes expected value as if every action were reversible. Writing off an invoice should need a far higher confidence bar than sending a twenty-rupee email. That's a cost-sensitive threshold per action.
>
> Three: run it online with a permanent holdout arm. The same argument that makes this simulation legitimate is the argument for keeping a random control group in production — otherwise the lift stops being measurable the moment it ships.
>
> Repo's linked, it runs from a clean clone in seven commands, and every number in the report is computed rather than typed. Thanks for watching.

---

## Recording notes

- **Third take minimum.** Takes one and two are for finding where you ramble.
- The strongest 30 seconds is "two of the four are negative" — slow down there. Anyone can show a chart going up; volunteering where your own system loses money is the part that reads as engineering judgment.
- Say **"one crore sixty-eight lakh"**, not "one point six seven crore".
- Do not apologise for the simulator. Section 2 is the argument for it; if it lands, nobody asks.
- Hard cap 5:00. If the last take is 5:07, cut the third item in section 6, not the counterfactual.
