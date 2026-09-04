"""
simulator.py — invents 500 fake-but-realistic overdue invoices.

Plain English: this file writes the dataset. Each invoice gets a hidden reason
it is unpaid (the `latent_cause`), a set of visible clues that *hint* at that
reason without giving it away, and a few pre-rolled dice the world model will
need later.

Two design decisions here matter more than the rest:

1. ALL RANDOMNESS HAPPENS HERE, ONCE. When a cash-strapped customer's money
   arrives, whether they will promise to pay on a phone call, when they will
   opt out of being contacted — all of it is drawn now and stamped onto the
   invoice. The world model then rolls no dice at all, which is what makes
   replaying two different policies over the same invoice a fair comparison.

2. THE CLUES ARE DELIBERATELY IMPERFECT. Roughly 10% of invoices have their
   clues generated from the WRONG cause's parameters. Without that the
   classifier in Phase 3 scores 100%, and a 100% score is a confession that
   the data is fake.

Columns beginning `hidden_`, plus `latent_cause`, are ground truth owned by the
world model. No policy and no classifier is permitted to read them — see
`config.OBSERVABLE_COLUMNS`.
"""

from __future__ import annotations

import os

import numpy as np
import pandas as pd

import config as cfg
from src.world_model import InvoiceState


# ---------------------------------------------------------------------------
# Clue generation
# ---------------------------------------------------------------------------


def _draw_clues(rng: np.random.Generator, clue_cause: str) -> dict:
    """Generate the observable features for one invoice.

    `clue_cause` is normally the invoice's true cause — but for the ~10% of
    noisy invoices it is deliberately a different one, which is how a
    misleading invoice gets made.
    """
    p = cfg.CLUE_PARAMS[clue_cause]

    dso = rng.normal(p["historic_dso_mean"], p["historic_dso_sd"])
    dso = int(np.clip(round(dso), cfg.HISTORIC_DSO_MIN, cfg.HISTORIC_DSO_MAX))

    prior_disputes = int(min(rng.poisson(p["prior_disputes_lambda"]), cfg.PRIOR_DISPUTES_MAX))
    prior_writeoffs = int(min(rng.poisson(p["prior_writeoffs_lambda"]), cfg.PRIOR_WRITEOFFS_MAX))

    po_mismatch = bool(rng.random() < p["po_mismatch_p"])
    partial_delivery = bool(rng.random() < p["partial_delivery_p"])

    pays_after_dom = int(rng.integers(p["pays_after_dom_min"], p["pays_after_dom_max"] + 1))

    # Engagement on the ORIGINAL invoice email, measured before any chasing
    # begins. Total silence is the CHRONIC tell.
    email_opened = bool(rng.random() < p["email_opened_p"])
    email_replied = bool(email_opened and rng.random() < p["email_replied_given_opened_p"])

    return {
        "customer_historic_dso": dso,
        "customer_prior_disputes": prior_disputes,
        "customer_prior_writeoffs": prior_writeoffs,
        "po_mismatch_flag": po_mismatch,
        "partial_delivery_flag": partial_delivery,
        "customer_pays_after_day_of_month": pays_after_dom,
        "email_opened": email_opened,
        "email_replied": email_replied,
    }


def _draw_amount(rng: np.random.Generator) -> float:
    """Invoice value in INR. Lognormal — a lot of small ones, a long fat tail."""
    raw = float(rng.lognormal(cfg.AMOUNT_LOG_MEAN, cfg.AMOUNT_LOG_SIGMA))
    raw = float(np.clip(raw, cfg.AMOUNT_MIN, cfg.AMOUNT_MAX))
    return float(round(raw / cfg.AMOUNT_ROUND_TO) * cfg.AMOUNT_ROUND_TO)


def _draw_causes(rng: np.random.Generator, n: int) -> np.ndarray:
    """Assign hidden causes by exact quota, then shuffle.

    Quota rather than independent sampling, so the realised mix is as close to
    35/25/20/20 as integer division allows and Gate 1's +/-3% check is about
    the code being right, not about luck.
    """
    counts = {c: int(round(cfg.CAUSE_SHARES[c] * n)) for c in cfg.CAUSES}
    # Absorb any rounding drift into the largest bucket.
    drift = n - sum(counts.values())
    counts[cfg.FORGOTTEN] += drift

    causes = np.array([c for c in cfg.CAUSES for _ in range(counts[c])], dtype=object)
    if len(causes) != n:
        raise ValueError(f"cause quota produced {len(causes)} invoices, expected {n}")
    rng.shuffle(causes)
    return causes


# ---------------------------------------------------------------------------
# The generator
# ---------------------------------------------------------------------------


def generate_invoices(n: int = cfg.N_INVOICES, seed: int = cfg.SEED) -> pd.DataFrame:
    """Build the full invoice table. Same seed always gives the same table."""
    rng = np.random.default_rng(seed)

    causes = _draw_causes(rng, n)
    due_start = pd.Timestamp(cfg.DUE_DATE_START)

    rows = []
    for i in range(n):
        true_cause = str(causes[i])

        # ~10% of invoices get their clues drawn from a DIFFERENT cause. This
        # is the noise that stops the classifier from being perfect.
        is_noisy = bool(rng.random() < cfg.CLUE_NOISE_RATE)
        if is_noisy:
            others = [c for c in cfg.CAUSES if c != true_cause]
            clue_cause = str(others[int(rng.integers(0, len(others)))])
        else:
            clue_cause = true_cause

        clues = _draw_clues(rng, clue_cause)

        # --- pre-rolled dice the world model will need later ---------------

        if true_cause == cfg.CASH_CRUNCH:
            liquidity_day = int(rng.integers(
                cfg.CASH_CRUNCH_LIQUIDITY_DAY_MIN,
                cfg.CASH_CRUNCH_LIQUIDITY_DAY_MAX + 1,
            ))
        else:
            liquidity_day = -1  # sentinel: not applicable

        ptp_will_promise = bool(rng.random() < cfg.PTP_PROBABILITY_BY_CAUSE[true_cause])
        ptp_delay_days = int(rng.integers(cfg.PTP_DELAY_DAYS_MIN, cfg.PTP_DELAY_DAYS_MAX + 1))

        if rng.random() < cfg.OPTOUT_PROBABILITY_BY_CAUSE[true_cause]:
            choices = cfg.OPTOUT_AFTER_CONTACTS_CHOICES
            optout_after = int(choices[int(rng.integers(0, len(choices)))])
        else:
            optout_after = 0  # never opts out

        rows.append({
            # --- observable ------------------------------------------------
            "invoice_id": f"INV-{i + 1:04d}",
            "customer_id": f"CUST-{i + 1:04d}",
            "amount": _draw_amount(rng),
            "due_date": (due_start + pd.Timedelta(
                days=int(rng.integers(0, cfg.DUE_DATE_SPREAD_DAYS))
            )).date().isoformat(),
            **clues,
            "is_msme_supplier": bool(rng.random() < cfg.IS_MSME_SUPPLIER_P),
            # --- hidden ----------------------------------------------------
            "latent_cause": true_cause,
            "hidden_liquidity_day": liquidity_day,
            "hidden_ptp_will_promise": ptp_will_promise,
            "hidden_ptp_delay_days": ptp_delay_days,
            "hidden_optout_after_contacts": optout_after,
            "hidden_clue_noise": is_noisy,
        })

    df = pd.DataFrame(rows)[list(cfg.ALL_COLUMNS)]
    return df


# ---------------------------------------------------------------------------
# Disk I/O
# ---------------------------------------------------------------------------


def write_invoices(df: pd.DataFrame, path: str = cfg.INVOICES_CSV) -> str:
    """Write the invoice table to CSV, creating the data directory if needed."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    df.to_csv(path, index=False)
    return path


def load_invoices(path: str = cfg.INVOICES_CSV) -> pd.DataFrame:
    """Read the invoice table back. Raises loudly if it was never generated."""
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"{path} does not exist. Generate it first:  python run.py --generate"
        )
    df = pd.read_csv(path)
    missing = [c for c in cfg.ALL_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"{path} is missing expected columns: {missing}")
    return df


# ---------------------------------------------------------------------------
# Bridge from a CSV row to a world-model state
# ---------------------------------------------------------------------------


def to_state(row) -> InvoiceState:
    """Turn one row of the invoice table into a fresh day-0 InvoiceState.

    Called once per invoice per policy run. Because the state starts clean every
    time, the baseline run cannot contaminate the agent run.
    """
    liquidity = int(row["hidden_liquidity_day"])
    return InvoiceState(
        invoice_id=str(row["invoice_id"]),
        amount=float(row["amount"]),
        latent_cause=str(row["latent_cause"]),
        liquidity_day=liquidity if liquidity >= 0 else None,
        ptp_will_promise=bool(row["hidden_ptp_will_promise"]),
        ptp_delay_days=int(row["hidden_ptp_delay_days"]),
        optout_after_contacts=int(row["hidden_optout_after_contacts"]),
    )


def observable_view(row) -> dict:
    """The slice of an invoice a policy is allowed to see.

    Every policy takes its input through this function. If a policy wants to
    peek at `latent_cause`, it has to visibly go around this — which is the
    point.
    """
    return {col: row[col] for col in cfg.OBSERVABLE_COLUMNS}
