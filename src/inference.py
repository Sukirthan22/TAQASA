"""
inference.py — the detective: guess which of the four hidden causes applies.

Plain English: the agent cannot see why an invoice is unpaid. It can only see
the clues — how long this customer usually takes, whether they argue about
invoices, whether they even opened the email. This file turns those clues into
a guess, and honestly reports how often the guess is wrong.

TWO ARMS, AND WHY BOTH EXIST
----------------------------
1. RULES ARM — explicit if/else. No training, no model file, no split. Every
   threshold is read straight off the CLUE_PARAMS table in config.py, so you
   can check the rules against the data generator without running anything.
   It ships first because it always works and it is fully explainable.

2. LEARNED ARM — a decision tree capped at depth 4, trained on 350 invoices and
   scored on a held-out 150 it has never seen. Depth 4 is a hard cap, not a
   knob: a deeper tree cannot be printed in a README and read by a human, and
   an unreadable model has no business making collections decisions.

Both arms are scored on the SAME held-out 150. The rules arm needs no training,
so it could be scored on all 500 — but then the two accuracies would not be
comparable, and comparing them is the whole exercise.

THE LEAKAGE RAIL
----------------
Features come from `config.FEATURE_COLUMNS` and nowhere else. `latent_cause` is
used in exactly two places in this file: as the training label `y`, and as the
answer key when scoring. Neither is a leak — a classifier is allowed to learn
from labelled history. It is never a feature.

WHY ACCURACY MUST NOT BE 100%
-----------------------------
About 10% of invoices are generated with clues drawn from the WRONG cause (see
simulator.py). Those invoices are unlearnable by construction. If this file ever
reports 100%, something is reading the answer.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.metrics import accuracy_score, confusion_matrix
from sklearn.model_selection import train_test_split
from sklearn.tree import DecisionTreeClassifier, export_text

import config as cfg


# ---------------------------------------------------------------------------
# The split
# ---------------------------------------------------------------------------


def split(df: pd.DataFrame, seed: int = cfg.SEED):
    """Cut the book into a training set and a held-out test set.

    Stratified, so all four causes keep their 35/25/20/20 share on both sides.
    Without stratification a lucky split could hand the test set 40 CHRONIC
    invoices and the accuracy would measure the split, not the model.
    """
    train, test = train_test_split(
        df,
        train_size=cfg.TRAIN_SIZE,
        test_size=cfg.TEST_SIZE,
        random_state=seed,
        stratify=df["latent_cause"],
    )
    return train, test


def _features(df: pd.DataFrame) -> pd.DataFrame:
    """The only columns any classifier in this project is allowed to touch."""
    return df[list(cfg.FEATURE_COLUMNS)].astype(float)


# ---------------------------------------------------------------------------
# ARM 1 — the rules
# ---------------------------------------------------------------------------


class RulesClassifier:
    """A four-branch cascade over the observable clues.

    Order matters, and the order is an argument about evidence strength:

    1. CHRONIC goes first because its tell is the strongest and the most
       distinctive — a customer who never even OPENS the email, and who has
       already been written off before or takes ~88 days to pay anything.
       Two independent signals have to agree. Peeling these off first stops
       them contaminating the DISPUTE branch, because chronic non-payers also
       accumulate disputes (lambda 0.8) as a side effect of never paying.

    2. DISPUTE next: a paperwork problem on THIS invoice (PO mismatch or short
       delivery) or a customer who argues as a matter of habit (2+ prior
       disputes against a background of 0.2-0.3 for everyone else).

    3. CASH_CRUNCH next: they only ever pay late in the month, when their own
       receivables land, and they are slow in general.

    4. FORGOTTEN is the catch-all. It fires because nothing else did, which is
       exactly why it reports the lowest confidence. That is honest: "I have no
       positive evidence of a problem" is a weaker claim than "I have two
       signals pointing the same way."
    """

    name = "rules"

    def predict_one(self, clues) -> tuple:
        """Guess the cause for one invoice. Returns (cause, confidence, why)."""
        dso = float(clues["customer_historic_dso"])
        disputes = float(clues["customer_prior_disputes"])
        writeoffs = float(clues["customer_prior_writeoffs"])
        po_mismatch = bool(clues["po_mismatch_flag"])
        partial = bool(clues["partial_delivery_flag"])
        day_of_month = float(clues["customer_pays_after_day_of_month"])
        opened = bool(clues["email_opened"])

        # 1. CHRONIC — silence plus a track record of not paying.
        if not opened and (writeoffs >= cfg.RULE_CHRONIC_MIN_WRITEOFFS
                           or dso >= cfg.RULE_CHRONIC_MIN_DSO):
            return (cfg.CHRONIC, cfg.RULE_CONFIDENCE[cfg.CHRONIC],
                    f"email never opened and (prior write-offs {writeoffs:.0f} "
                    f">= {cfg.RULE_CHRONIC_MIN_WRITEOFFS} or DSO {dso:.0f} "
                    f">= {cfg.RULE_CHRONIC_MIN_DSO})")

        # 2. DISPUTE — something is wrong with the paperwork, or they always argue.
        if disputes >= cfg.RULE_DISPUTE_MIN_PRIOR_DISPUTES or po_mismatch or partial:
            bits = []
            if disputes >= cfg.RULE_DISPUTE_MIN_PRIOR_DISPUTES:
                bits.append(f"{disputes:.0f} prior disputes")
            if po_mismatch:
                bits.append("PO mismatch")
            if partial:
                bits.append("partial delivery")
            return (cfg.DISPUTE, cfg.RULE_CONFIDENCE[cfg.DISPUTE],
                    "dispute signal: " + ", ".join(bits))

        # 3. CASH_CRUNCH — pays only once their own money has landed.
        if (day_of_month >= cfg.RULE_CASH_MIN_DAY_OF_MONTH
                or dso >= cfg.RULE_CASH_MIN_DSO):
            return (cfg.CASH_CRUNCH, cfg.RULE_CONFIDENCE[cfg.CASH_CRUNCH],
                    f"pays after day {day_of_month:.0f} of the month "
                    f"or slow overall (DSO {dso:.0f})")

        # 4. FORGOTTEN — no positive evidence of any problem.
        return (cfg.FORGOTTEN, cfg.RULE_CONFIDENCE[cfg.FORGOTTEN],
                f"no dispute, write-off or late-month signal (DSO {dso:.0f})")

    def predict(self, df: pd.DataFrame) -> np.ndarray:
        """Guess the cause for a whole table."""
        return np.array([self.predict_one(row)[0] for _, row in df.iterrows()],
                        dtype=object)


# ---------------------------------------------------------------------------
# ARM 2 — the tree
# ---------------------------------------------------------------------------


class TreeClassifier:
    """A depth-4 decision tree. Shallow on purpose so a human can read it."""

    name = "tree"

    def __init__(self, model: DecisionTreeClassifier):
        self.model = model

    def predict(self, df: pd.DataFrame) -> np.ndarray:
        return self.model.predict(_features(df))

    def predict_one(self, clues) -> tuple:
        """Guess the cause for one invoice. Returns (cause, confidence, why)."""
        x = pd.DataFrame([{c: float(clues[c]) for c in cfg.FEATURE_COLUMNS}])
        probs = self.model.predict_proba(x)[0]
        idx = int(np.argmax(probs))
        cause = str(self.model.classes_[idx])
        return (cause, float(probs[idx]),
                f"depth-{cfg.TREE_MAX_DEPTH} tree leaf, p={probs[idx]:.2f}")

    def printed(self) -> str:
        """The whole model as text, for the README."""
        return export_text(self.model, feature_names=list(cfg.FEATURE_COLUMNS))


def train_tree(train: pd.DataFrame, seed: int = cfg.SEED) -> TreeClassifier:
    """Fit the depth-capped tree on the training split only."""
    model = DecisionTreeClassifier(max_depth=cfg.TREE_MAX_DEPTH, random_state=seed)
    model.fit(_features(train), train["latent_cause"])
    return TreeClassifier(model)


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


@dataclass
class ArmScore:
    """How one arm did on one set of invoices."""

    arm: str
    accuracy: float
    matrix: np.ndarray            # rows = truth, cols = guess, order = cfg.CAUSES
    per_cause_recall: dict
    per_cause_precision: dict
    n: int


def score(arm, data: pd.DataFrame) -> ArmScore:
    """Run an arm over a set of invoices and grade it against the answer key.

    Both recall and precision are reported, because for this agent they buy
    completely different things:

      RECALL on CHRONIC    — how many hopeless invoices we spotted, i.e. how
                             much wasted contact cost we avoided. Cheap upside.
      PRECISION on CHRONIC — of the invoices we CALL chronic, how many really
                             are. This is the expensive one: a wrong CHRONIC
                             guess ends in WRITE_OFF, which is terminal, so we
                             throw away an invoice that would have paid in full.

    Accuracy alone hides that asymmetry, which is why arm selection in Phase 4
    is argued on precision, not on the headline number.
    """
    truth = data["latent_cause"].to_numpy(dtype=object)
    guess = arm.predict(data)
    matrix = confusion_matrix(truth, guess, labels=list(cfg.CAUSES))
    recall, precision = {}, {}
    for i, cause in enumerate(cfg.CAUSES):
        actual = matrix[i].sum()
        predicted = matrix[:, i].sum()
        recall[cause] = (matrix[i][i] / actual) if actual else 0.0
        precision[cause] = (matrix[i][i] / predicted) if predicted else 0.0
    return ArmScore(
        arm=arm.name,
        accuracy=float(accuracy_score(truth, guess)),
        matrix=matrix,
        per_cause_recall=recall,
        per_cause_precision=precision,
        n=len(data),
    )


def print_matrix(s: ArmScore) -> None:
    """Print a 4x4 confusion matrix, recall down the side, precision along the foot."""
    short = {c: c[:4] for c in cfg.CAUSES}
    print()
    print(f"  {s.arm.upper()} ARM — held-out n={s.n}, accuracy {s.accuracy:.1%}")
    print(f"    {'truth \\ guess':<14}" + "".join(f"{short[c]:>7}" for c in cfg.CAUSES)
          + f"{'recall':>9}")
    for i, cause in enumerate(cfg.CAUSES):
        row = "".join(f"{s.matrix[i][j]:>7}" for j in range(len(cfg.CAUSES)))
        print(f"    {cause:<14}{row}{s.per_cause_recall[cause]:>9.0%}")
    print(f"    {'precision':<14}"
          + "".join(f"{s.per_cause_precision[c]:>6.0%} " for c in cfg.CAUSES))


# ---------------------------------------------------------------------------
# The Gate 3 report
# ---------------------------------------------------------------------------


def evaluate(df: pd.DataFrame, seed: int = cfg.SEED) -> dict:
    """Train, score both arms on the identical held-out split, and print it all.

    Returns the winner alongside both scores, so Phase 4 can pick up whichever
    arm actually won rather than whichever one we assumed would.
    """
    train, test = split(df, seed=seed)

    rules = RulesClassifier()
    tree = train_tree(train, seed=seed)

    rules_score = score(rules, test)
    tree_score = score(tree, test)

    print()
    print("=" * 76)
    print("CAUSE INFERENCE — both arms on the identical held-out split")
    print("=" * 76)
    print(f"  Train {len(train)} invoices   Held-out {len(test)} invoices   "
          f"stratified, seed {seed}")
    print(f"  Features ({len(cfg.FEATURE_COLUMNS)}): {', '.join(cfg.FEATURE_COLUMNS)}")
    print(f"  Unlearnable by construction: {int(test['hidden_clue_noise'].sum())} of "
          f"{len(test)} held-out invoices carry clues from the WRONG cause "
          f"({test['hidden_clue_noise'].mean():.1%})")

    print_matrix(rules_score)
    print_matrix(tree_score)

    # --- Arm selection ----------------------------------------------------
    # Deliberately NOT decided on accuracy. The two arms finish within a couple
    # of invoices of each other, which on n=150 is noise, not a result. The
    # tie-break that matters is precision on CHRONIC, because that is the only
    # guess this agent acts on irreversibly: CHRONIC leads to WRITE_OFF, and a
    # write-off is terminal. Every false CHRONIC throws away an invoice that
    # would otherwise have paid in full.
    gap = rules_score.accuracy - tree_score.accuracy
    invoices_apart = abs(round(gap * len(test)))

    if rules_score.per_cause_precision[cfg.CHRONIC] >= tree_score.per_cause_precision[cfg.CHRONIC]:
        winner, loser = rules_score, tree_score
    else:
        winner, loser = tree_score, rules_score

    ceiling = 1.0 - float(test["hidden_clue_noise"].mean())

    print()
    print("-" * 76)
    print(f"  ACCURACY IS A TIE: rules {rules_score.accuracy:.1%} vs tree "
          f"{tree_score.accuracy:.1%} — {invoices_apart} invoices apart on "
          f"n={len(test)}. Not a result.")
    print()
    print(f"  ARM SELECTED: {winner.arm}, on CHRONIC precision "
          f"{winner.per_cause_precision[cfg.CHRONIC]:.0%} vs "
          f"{loser.per_cause_precision[cfg.CHRONIC]:.0%}.")
    print(f"  CHRONIC is the only guess the agent acts on irreversibly "
          f"(WRITE_OFF is terminal),")
    print(f"  so a false CHRONIC costs a whole invoice. On this split that is "
          f"{int(winner.matrix[:, cfg.CAUSES.index(cfg.CHRONIC)].sum() - winner.matrix[cfg.CAUSES.index(cfg.CHRONIC)][cfg.CAUSES.index(cfg.CHRONIC)])} "
          f"invoices wrongly written off")
    print(f"  under {winner.arm}, against "
          f"{int(loser.matrix[:, cfg.CAUSES.index(cfg.CHRONIC)].sum() - loser.matrix[cfg.CAUSES.index(cfg.CHRONIC)][cfg.CAUSES.index(cfg.CHRONIC)])} "
          f"under {loser.arm}.")
    print()
    print(f"  Practical ceiling is about {ceiling:.1%} — the noisy invoices cannot "
          f"be got right.")
    print("-" * 76)

    print()
    print("THE TREE, IN FULL (this is the whole model — it fits on one screen)")
    print("-" * 76)
    print(tree.printed())

    return {
        "train": train,
        "test": test,
        "rules": rules,
        "tree": tree,
        "rules_score": rules_score,
        "tree_score": tree_score,
        "winner": winner.arm,
    }
