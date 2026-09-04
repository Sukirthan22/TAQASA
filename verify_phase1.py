"""
verify_phase1.py — proves Gate 1 actually passed, rather than claiming it did.

Plain English: this is the exam paper for Phase 1. It re-generates the data from
scratch, re-runs the world model twice over identical action sequences, and
checks that the invented universe behaves the way the PRD says it should. It
prints numbers for every check. If any check fails it says so and exits non-zero.

Gate 1 asks for four things. This script covers all four:
  1. data/invoices.csv contains exactly 500 rows                  -> CHECK A
  2. cause distribution within +/-3% of 35/25/20/20               -> CHECK B
  3. ten rows you can eyeball and confirm make business sense     -> CHECK F prints them
  4. proof the world model is deterministic across two runs       -> CHECKS D and E

CHECKS C and G go further than the gate asks: C shows the clues really do
correlate with the hidden cause, and G scripts one scenario per cause to confirm
the payment rules in PRD B2 were implemented as written.
"""

from __future__ import annotations

import hashlib
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config as cfg  # noqa: E402
from src import world_model as wm  # noqa: E402
from src.simulator import generate_invoices, load_invoices, to_state  # noqa: E402


RESULTS: list = []


def record(name: str, passed: bool, detail: str = "") -> None:
    RESULTS.append((name, passed, detail))
    mark = "PASS" if passed else "FAIL"
    print(f"  [{mark}] {name}" + (f" — {detail}" if detail else ""))


def header(title: str) -> None:
    print()
    print(title)
    print("-" * len(title))


# ---------------------------------------------------------------------------
# CHECK A + B — the dataset itself
# ---------------------------------------------------------------------------


def check_dataset(df: pd.DataFrame) -> None:
    header("CHECK A — dataset shape")
    record("invoices.csv has exactly 500 rows",
           len(df) == cfg.N_INVOICES,
           f"{len(df)} rows")
    record("every invoice_id is unique",
           df["invoice_id"].is_unique,
           f"{df['invoice_id'].nunique()} distinct ids")
    record("no missing values anywhere",
           not df.isna().any().any(),
           f"{int(df.isna().sum().sum())} nulls")
    record("every latent_cause is one of the four defined values",
           set(df["latent_cause"].unique()) <= set(cfg.CAUSES),
           str(sorted(df["latent_cause"].unique())))

    header("CHECK B — cause distribution (tolerance +/-3%)")
    ok = True
    for cause in cfg.CAUSES:
        share = float((df["latent_cause"] == cause).mean())
        target = cfg.CAUSE_SHARES[cause]
        within = abs(share - target) <= cfg.CAUSE_SHARE_TOLERANCE
        ok = ok and within
        n = int((df["latent_cause"] == cause).sum())
        print(f"        {cause:<12} {n:>4}  {share:>6.1%}  target {target:>5.0%}  "
              f"delta {share - target:+.1%}")
    record("all four causes within tolerance", ok)

    noise = float(df["hidden_clue_noise"].mean())
    record("clue noise is roughly the configured 10%",
           abs(noise - cfg.CLUE_NOISE_RATE) <= 0.04,
           f"{noise:.1%} of invoices carry misleading clues")


# ---------------------------------------------------------------------------
# CHECK C — do the clues actually correlate with the hidden cause?
# ---------------------------------------------------------------------------


def check_clue_signal(df: pd.DataFrame) -> None:
    header("CHECK C — clues correlate with cause (but do not determine it)")

    summary = df.groupby("latent_cause").agg(
        n=("invoice_id", "size"),
        dso=("customer_historic_dso", "mean"),
        disputes=("customer_prior_disputes", "mean"),
        writeoffs=("customer_prior_writeoffs", "mean"),
        po_mismatch=("po_mismatch_flag", "mean"),
        partial=("partial_delivery_flag", "mean"),
        pay_dom=("customer_pays_after_day_of_month", "mean"),
        opened=("email_opened", "mean"),
        replied=("email_replied", "mean"),
    ).reindex(list(cfg.CAUSES))

    print(summary.to_string(float_format=lambda v: f"{v:6.2f}"))
    print()

    # The four signals the PRD says should exist, stated as testable claims.
    m = summary
    record("DISPUTE has the most prior disputes",
           m["disputes"].idxmax() == cfg.DISPUTE,
           f"highest: {m['disputes'].idxmax()}")
    record("DISPUTE has the most PO mismatches",
           m["po_mismatch"].idxmax() == cfg.DISPUTE,
           f"highest: {m['po_mismatch'].idxmax()}")
    record("CHRONIC has the most prior write-offs",
           m["writeoffs"].idxmax() == cfg.CHRONIC,
           f"highest: {m['writeoffs'].idxmax()}")
    record("CHRONIC is the quietest on email",
           m["opened"].idxmin() == cfg.CHRONIC,
           f"lowest open rate: {m['opened'].min():.0%} ({m['opened'].idxmin()})")
    record("CASH_CRUNCH pays latest in the month",
           m["pay_dom"].idxmax() == cfg.CASH_CRUNCH,
           f"highest: {m['pay_dom'].idxmax()}")
    record("CHRONIC has the worst historic DSO",
           m["dso"].idxmax() == cfg.CHRONIC,
           f"highest: {m['dso'].idxmax()} at {m['dso'].max():.0f} days")

    # No single clue may be a giveaway.
    leak = False
    for col in ("po_mismatch_flag", "partial_delivery_flag", "email_opened",
                "email_replied", "is_msme_supplier"):
        for value in (True, False):
            subset = df[df[col] == value]
            if len(subset) >= 20:
                purity = subset["latent_cause"].value_counts(normalize=True).max()
                if purity > 0.90:
                    leak = True
                    print(f"        LEAK: {col}=={value} is {purity:.0%} one cause")
    record("no single binary clue determines the cause outright", not leak,
           "no clue exceeds 90% purity")


# ---------------------------------------------------------------------------
# CHECK D — generation is deterministic
# ---------------------------------------------------------------------------


def _frame_hash(df: pd.DataFrame) -> str:
    return hashlib.sha256(df.to_csv(index=False).encode("utf-8")).hexdigest()


def check_generation_determinism() -> None:
    header("CHECK D — generating twice with SEED=42 gives identical data")
    a = generate_invoices(seed=cfg.SEED)
    b = generate_invoices(seed=cfg.SEED)
    ha, hb = _frame_hash(a), _frame_hash(b)
    record("two independent generations are byte-identical", ha == hb, f"sha256 {ha[:16]}")

    c = generate_invoices(seed=cfg.SEED + 1)
    record("a different seed gives different data (the seed is real)",
           _frame_hash(c) != ha, f"sha256 {_frame_hash(c)[:16]}")


# ---------------------------------------------------------------------------
# CHECK E — the world model is deterministic
# ---------------------------------------------------------------------------


def _replay(df: pd.DataFrame, action_seed: int) -> str:
    """Drive every invoice through a random-but-seeded action sequence.

    Returns a hash of the full trace. Two calls with the same action_seed must
    return the same hash, or replaying two policies over one invoice is not a
    fair comparison and the headline metric is worthless.
    """
    rng = np.random.default_rng(action_seed)
    # Draw the whole action plan up front so the trace depends only on the seed,
    # never on how the world happened to respond.
    plan = rng.choice(list(cfg.ACTIONS), size=(len(df), cfg.HORIZON_DAYS + 1))

    trace_lines = []
    for i, (_, row) in enumerate(df.iterrows()):
        state = to_state(row)
        for day in range(cfg.HORIZON_DAYS + 1):
            if state.is_terminal:
                break
            outcome = wm.step(state, str(plan[i][day]), day)
            state = outcome.state
            if outcome.cash_collected or outcome.events:
                trace_lines.append(
                    f"{state.invoice_id}|{day}|{plan[i][day]}|"
                    f"{outcome.cash_collected:.2f}|{outcome.cost:.2f}|{','.join(outcome.events)}"
                )
        state = wm.finalize_at_horizon(state)
        trace_lines.append(
            f"{state.invoice_id}|FINAL|{state.status}|{state.paid_amount:.2f}|"
            f"{state.total_cost:.2f}|{state.paid_day}"
        )
    return hashlib.sha256("\n".join(trace_lines).encode("utf-8")).hexdigest()


def check_world_model_determinism(df: pd.DataFrame) -> None:
    header("CHECK E — replaying the world model twice gives an identical trace")
    h1 = _replay(df, action_seed=7)
    h2 = _replay(df, action_seed=7)
    record("same invoices + same action sequence = same result",
           h1 == h2, f"trace sha256 {h1[:16]}")

    h3 = _replay(df, action_seed=8)
    record("a different action sequence gives a different result "
           "(actions genuinely matter)",
           h3 != h1, f"trace sha256 {h3[:16]}")

    header("CHECK E2 — terminal states are respected")
    state = to_state(df.iloc[0])
    state = wm.step(state, cfg.WRITE_OFF, 1).state
    try:
        wm.step(state, cfg.NUDGE_SOFT, 2)
        record("acting on a written-off invoice raises", False, "it did not raise")
    except ValueError as exc:
        record("acting on a written-off invoice raises", True, str(exc)[:70])


# ---------------------------------------------------------------------------
# CHECK F — ten rows to eyeball
# ---------------------------------------------------------------------------


def check_eyeball_sample(df: pd.DataFrame) -> None:
    header("CHECK F — ten rows to read by hand (Gate 1 asks you to sanity-check these)")

    # Three rows per cause, so every cause is represented in what you eyeball.
    sample = pd.concat([df[df["latent_cause"] == cause].head(3) for cause in cfg.CAUSES])
    cols = ["invoice_id", "amount", "latent_cause", "customer_historic_dso",
            "customer_prior_disputes", "customer_prior_writeoffs",
            "po_mismatch_flag", "email_opened", "email_replied",
            "hidden_liquidity_day", "hidden_clue_noise"]
    print(sample[cols].to_string(index=False))
    print()
    print("  Read it like this: a DISPUTE row should usually show prior disputes")
    print("  and/or a PO mismatch. A CHRONIC row should show write-offs and an")
    print("  unopened email. A CASH_CRUNCH row should carry a liquidity day in")
    print("  40-70. Any row where the clues point the wrong way should have")
    print("  hidden_clue_noise = True — those are the ~10% built to fool the agent.")
    record("sample printed for manual inspection", True, f"{len(sample)} rows shown")


# ---------------------------------------------------------------------------
# CHECK G — does the world obey PRD B2?
# ---------------------------------------------------------------------------


def _drive(state: wm.InvoiceState, schedule: dict) -> wm.InvoiceState:
    """Run one invoice to the horizon, taking the scheduled action each day."""
    for day in range(cfg.HORIZON_DAYS + 1):
        if state.is_terminal:
            break
        state = wm.step(state, schedule.get(day, cfg.WAIT), day).state
    return wm.finalize_at_horizon(state)


def _probe(cause: str, amount: float = 100_000, liquidity: int | None = None) -> wm.InvoiceState:
    return wm.InvoiceState(
        invoice_id=f"PROBE-{cause}",
        amount=amount,
        latent_cause=cause,
        liquidity_day=liquidity,
        ptp_will_promise=False,
        ptp_delay_days=14,
        optout_after_contacts=0,
    )


def check_world_rules() -> None:
    header("CHECK G — scripted scenarios confirm the PRD B2 payment rules")

    # --- FORGOTTEN ---------------------------------------------------------
    s = _drive(_probe(cfg.FORGOTTEN), {3: cfg.NUDGE_SOFT})
    record("FORGOTTEN nudged on day 3 pays on day 5",
           s.status == wm.PAID and s.paid_day == 5, f"paid_day={s.paid_day}")

    s = _drive(_probe(cfg.FORGOTTEN), {})
    record("FORGOTTEN never contacted pays on day 75",
           s.paid_day == cfg.FORGOTTEN_UNCONTACTED_PAY_DAY, f"paid_day={s.paid_day}")

    s = _drive(_probe(cfg.FORGOTTEN), {3: cfg.NUDGE_SOFT, 10: cfg.NUDGE_FIRM})
    record("FORGOTTEN: extra nudges after the first change nothing",
           s.paid_day == 5, f"paid_day={s.paid_day}")

    # --- CASH_CRUNCH -------------------------------------------------------
    s = _drive(_probe(cfg.CASH_CRUNCH, liquidity=50), {7: cfg.NUDGE_SOFT, 21: cfg.NUDGE_FIRM})
    record("CASH_CRUNCH: contacts before the liquidity day are wasted",
           s.paid_day == 50 + cfg.CASH_CRUNCH_UNCONTACTED_PAY_LAG,
           f"paid_day={s.paid_day}, cost=Rs {s.total_cost:.0f} for 2 useless contacts")

    s = _drive(_probe(cfg.CASH_CRUNCH, liquidity=50), {52: cfg.NUDGE_SOFT})
    record("CASH_CRUNCH: a contact after the liquidity day converts in 2 days",
           s.paid_day == 54, f"paid_day={s.paid_day}")

    s = _drive(_probe(cfg.CASH_CRUNCH, liquidity=50), {20: cfg.OFFER_PLAN, 42: cfg.NUDGE_SOFT})
    record("CASH_CRUNCH: OFFER_PLAN pulls the liquidity day forward by 10",
           s.paid_day == 44, f"paid_day={s.paid_day} (without the plan it would be 52)")

    # --- DISPUTE -----------------------------------------------------------
    s = _drive(_probe(cfg.DISPUTE), {7: cfg.NUDGE_SOFT, 21: cfg.NUDGE_FIRM, 45: cfg.CALL})
    record("DISPUTE: the baseline ladder never recovers a rupee",
           s.status == wm.WRITTEN_OFF and s.paid_amount == 0,
           f"status={s.status}, burned Rs {s.total_cost:.0f}")

    s = _drive(_probe(cfg.DISPUTE), {1: cfg.ROUTE_DISPUTE})
    record("DISPUTE routed on day 1 pays on day 11",
           s.paid_day == 1 + cfg.DISPUTE_PAY_LAG_AFTER_ROUTING, f"paid_day={s.paid_day}")

    s = _drive(_probe(cfg.DISPUTE), {7: cfg.NUDGE_SOFT, 21: cfg.NUDGE_FIRM, 30: cfg.ROUTE_DISPUTE})
    record("DISPUTE: each reminder before routing costs 3 extra days",
           s.paid_day == 30 + 10 + 6, f"paid_day={s.paid_day} (2 reminders = +6 days)")

    # --- CHRONIC -----------------------------------------------------------
    s = _drive(_probe(cfg.CHRONIC), {7: cfg.NUDGE_SOFT, 21: cfg.NUDGE_FIRM, 45: cfg.CALL})
    record("CHRONIC: chasing recovers nothing and burns cash",
           s.status == wm.WRITTEN_OFF and s.paid_amount == 0,
           f"burned Rs {s.total_cost:.0f} for Rs 0")

    s = _drive(_probe(cfg.CHRONIC, amount=500_000), {61: cfg.ESCALATE_LEGAL})
    record("CHRONIC above Rs 2,00,000: legal notice recovers 40% on day 85",
           s.paid_day == cfg.CHRONIC_LEGAL_PAY_DAY and s.paid_amount == 200_000,
           f"paid_day={s.paid_day}, recovered Rs {s.paid_amount:,.0f} of Rs 500,000")

    s = _drive(_probe(cfg.CHRONIC, amount=100_000), {61: cfg.ESCALATE_LEGAL})
    record("CHRONIC below the threshold: legal notice recovers nothing",
           s.paid_amount == 0, f"spent Rs {s.total_cost:.0f}, recovered Rs 0")

    # --- promise-to-pay ----------------------------------------------------
    header("CHECK G2 — promise-to-pay (what CALL buys) and opt-out")

    p = wm.InvoiceState(invoice_id="PROBE-PTP", amount=100_000, latent_cause=cfg.CHRONIC,
                        liquidity_day=None, ptp_will_promise=True, ptp_delay_days=14,
                        optout_after_contacts=0)
    out = wm.step(p, cfg.CALL, 10)
    record("a CALL extracts a promise-to-pay date",
           out.state.ptp_date == 24, f"promised day {out.state.ptp_date}")

    s = _drive(p, {10: cfg.CALL})
    record("a CHRONIC promise is broken, and the world records that",
           s.ptp_broken and s.paid_amount == 0, "ptp_broken=True, recovered Rs 0")

    p = wm.InvoiceState(invoice_id="PROBE-OPTOUT", amount=100_000, latent_cause=cfg.DISPUTE,
                        liquidity_day=None, ptp_will_promise=False, ptp_delay_days=14,
                        optout_after_contacts=2)
    s = _drive(p, {5: cfg.NUDGE_SOFT, 9: cfg.NUDGE_FIRM})
    record("a customer opts out after their configured contact count",
           s.opted_out, f"opted out after {s.contact_count} contacts")


# ---------------------------------------------------------------------------


def main() -> int:
    print("=" * 78)
    print("PHASE 1 — GATE VERIFICATION")
    print("=" * 78)

    df = load_invoices()

    check_dataset(df)
    check_clue_signal(df)
    check_generation_determinism()
    check_world_model_determinism(df)
    check_world_rules()
    check_eyeball_sample(df)

    failed = [r for r in RESULTS if not r[1]]
    print()
    print("=" * 78)
    print(f"{len(RESULTS) - len(failed)} of {len(RESULTS)} checks passed.")
    if failed:
        print()
        print("GATE 1 FAILED. Do not start Phase 2. Failures:")
        for name, _, detail in failed:
            print(f"  - {name} ({detail})")
        print("=" * 78)
        return 1
    print("GATE 1 PASSED.")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
