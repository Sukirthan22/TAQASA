"""
export_data.py — dumps everything the dashboard needs into web/data.json.

Plain English: the dashboard is a static page, so it cannot run the simulation.
This script runs it instead — both policies over the identical invoice book,
using the project's own harness — and writes the result out as one JSON file.

IT COMPUTES NOTHING OF ITS OWN. Every figure here comes from `harness.run_policy`
and `inference.score`, the same two functions `src/report.py` uses, so the
dashboard and results/report.md cannot drift apart. If a number looks wrong on
the page, it is wrong in the report too.

    python web/export_data.py

Strings are deduplicated into lookup tables (`reasons`, `guardrails`) and
decisions are stored as positional arrays, because the raw audit log is 1.1 MB
of mostly repeated text and this has to travel inside an HTML file.
"""

from __future__ import annotations

import json
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config as cfg  # noqa: E402
from src.harness import run_policy  # noqa: E402
from src.inference import RulesClassifier, score, split, train_tree  # noqa: E402
from src.policies.agent import build as build_agent  # noqa: E402
from src.policies.baseline import build as build_baseline  # noqa: E402
from src.simulator import load_invoices  # noqa: E402


class Pool:
    """Deduplicating string table: same string in, same index out."""

    def __init__(self):
        self.items: list = []
        self._index: dict = {}

    def add(self, value) -> int:
        if value is None:
            return -1
        if value not in self._index:
            self._index[value] = len(self.items)
            self.items.append(value)
        return self._index[value]


def cumulative(result) -> list:
    """Rupees collected on or before each day — same shape as report.py's chart."""
    daily = [0.0] * (cfg.HORIZON_DAYS + 1)
    for run in result.runs:
        if run.paid and run.paid_day is not None:
            daily[run.paid_day] += run.cash
    total, series = 0.0, []
    for value in daily:
        total += value
        series.append(round(total))
    return series


def arm_payload(name: str, s, deterministic: bool, cost: str) -> dict:
    return {
        "name": name,
        "accuracy": s.accuracy,
        "matrix": [[int(s.matrix[i][j]) for j in range(len(cfg.CAUSES))]
                   for i in range(len(cfg.CAUSES))],
        "recall": {c: s.per_cause_recall[c] for c in cfg.CAUSES},
        "precision": {c: s.per_cause_precision[c] for c in cfg.CAUSES},
        "deterministic": deterministic,
        "cost": cost,
    }


def main() -> int:
    df = load_invoices()
    baseline = run_policy(build_baseline(), df)
    agent = run_policy(build_agent(), df)

    guess = dict(zip(df["invoice_id"], RulesClassifier().predict(df)))
    truth = dict(zip(df["invoice_id"], df["latent_cause"]))
    bm = {r.invoice_id: r for r in baseline.runs}

    reasons, guardrails = Pool(), Pool()
    actions = list(cfg.ACTIONS)

    invoices = []
    veto_counts: Counter = Counter()
    action_mix = {"baseline": Counter(), "agent": Counter()}

    for run in agent.runs:
        b = bm[run.invoice_id]
        decisions = []
        for d in run.decisions:
            if d["guardrail_triggered"]:
                # The raw string carries per-invoice detail
                # ("C2.5_legal_not_eligible:amount_237800_overdue_12"), which is
                # useful in the decision feed but useless as a tally. Count by
                # the rule id in front of the colon.
                veto_counts[d["guardrail_triggered"].split(":")[0]] += 1
            if d["action"] != cfg.WAIT:
                action_mix["agent"][d["action"]] += 1
            decisions.append([
                d["day"],
                actions.index(d["action"]),
                reasons.add(d["reason"]),
                guardrails.add(d["guardrail_triggered"]),
                round(d["cash"]),
                round(d["cost"]),
                d["confidence"],
            ])

        invoices.append({
            "id": run.invoice_id,
            "amt": round(run.amount),
            "cause": run.latent_cause,
            "guess": guess[run.invoice_id],
            "status": run.final_state.status,
            # agent
            "aPaid": run.paid, "aDay": run.paid_day,
            "aCash": round(run.cash), "aCost": round(run.cost),
            "aContacts": run.contacts,
            # baseline, same invoice, same world
            "bPaid": b.paid, "bDay": b.paid_day,
            "bCash": round(b.cash), "bCost": round(b.cost),
            "bContacts": b.contacts,
            "dec": decisions,
        })

    for run in baseline.runs:
        for d in run.decisions:
            if d["action"] != cfg.WAIT:
                action_mix["baseline"][d["action"]] += 1

    # --- inference arms, scored on the same held-out split as the report ----
    train, test = split(df)
    arms = [
        arm_payload("rules", score(RulesClassifier(), test), True, "free"),
        arm_payload("depth-4 tree", score(train_tree(train), test), True, "free"),
    ]
    try:
        from src.llm_arm import LLMClassifier, load_all_cached

        for payload in load_all_cached():
            if payload.get("partial"):
                continue
            label = payload["model"].split("/")[-1]
            arms.append(arm_payload(label, score(LLMClassifier(payload), test),
                                    False, "~460k tokens"))
    except (FileNotFoundError, ImportError):
        pass

    floor = test["latent_cause"].value_counts().iloc[0] / len(test)

    # --- the failures, recomputed the same way report.py does --------------
    ids = [r.invoice_id for r in agent.runs]
    am = {r.invoice_id: r for r in agent.runs}
    f1 = [i for i in ids if guess[i] == cfg.CHRONIC and truth[i] != cfg.CHRONIC
          and bm[i].paid and not am[i].paid]
    routed = [i for i in ids if guess[i] == cfg.DISPUTE]
    f2 = [i for i in routed if truth[i] != cfg.DISPUTE]
    f4 = [i for i in ids if truth[i] == cfg.DISPUTE and guess[i] != cfg.DISPUTE]
    liquidity = dict(zip(df["invoice_id"], df["hidden_liquidity_day"]))
    cc = [i for i in ids if guess[i] == cfg.CASH_CRUNCH and truth[i] == cfg.CASH_CRUNCH]
    f3 = [i for i in cc
          if liquidity[i] > cfg.AGENT_CASH_CRUNCH_FIRST_CONTACT_DAY]

    payload = {
        "meta": {
            "seed": cfg.SEED,
            "invoices": len(df),
            "horizon": cfg.HORIZON_DAYS,
            "causes": list(cfg.CAUSES),
            "actions": actions,
            "actionCosts": cfg.ACTION_COSTS,
            "contactActions": sorted(cfg.CONTACT_ACTIONS),
            "guessCounts": dict(Counter(guess.values())),
            "inferenceFloor": floor,
            "noiseRate": cfg.CLUE_NOISE_RATE,
        },
        "reasons": reasons.items,
        "guardrails": guardrails.items,
        "overall": {"baseline": baseline.overall, "agent": agent.overall},
        "byCause": {c: {"baseline": baseline.by_cause[c], "agent": agent.by_cause[c]}
                    for c in cfg.CAUSES},
        "cumulative": {"baseline": cumulative(baseline), "agent": cumulative(agent)},
        "actionMix": {k: dict(v) for k, v in action_mix.items()},
        "vetoes": dict(veto_counts),
        "arms": arms,
        "failures": {
            "f1": {"ids": f1, "value": round(sum(bm[i].cash for i in f1))},
            "f2": {"routed": len(routed), "wrong": len(f2),
                   "cost": len(f2) * cfg.ACTION_COSTS[cfg.ROUTE_DISPUTE]},
            "f3": {"n": len(f3), "total": len(cc)},
            "f4": {"n": len(f4),
                   "value": round(sum(float(df.loc[df["invoice_id"] == i, "amount"].iloc[0])
                                      for i in f4))},
        },
        "invoices": invoices,
    }

    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data.json")
    with open(out, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, separators=(",", ":"))

    lift = agent.overall["net_recovery"] - baseline.overall["net_recovery"]
    print(f"wrote {out}  ({os.path.getsize(out) / 1024:.0f} KB)")
    print(f"  {len(invoices)} invoices, "
          f"{sum(len(i['dec']) for i in invoices):,} decisions, "
          f"{len(reasons.items)} distinct reasons")
    print(f"  net incremental recovery {lift:,.0f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
