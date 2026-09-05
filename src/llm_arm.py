"""
llm_arm.py — a third cause-inference arm, so the LLM claim is measured not asserted.

Plain English: give a language model the same clues the rules engine and the
decision tree get, ask it which of the four hidden causes it thinks applies, and
grade it with the identical scoring function. Then report what happened, whether
or not it flatters the LLM.

WHY THIS EXISTS
---------------
"We didn't use an LLM because it's the wrong tool" is an assertion. Anyone can
make it. Running one on the same held-out invoices and putting the number in the
table beside the other two arms turns it into a result. If the LLM wins, that is
worth knowing and the README should say so. If it loses to a depth-4 decision
tree on ten tabular features, that is a more useful finding than any LLM feature
that merely works.

WHAT THIS ARM IS NOT ALLOWED TO DO
----------------------------------
It never influences a pay/wait/write-off decision (PRD C3). `policies/agent.py`
does not import this module and never will. Delete this file and every number in
results/report.md is unchanged. It classifies, it gets graded, it goes home.

THE CACHE, AND WHY IT IS NOT CHEATING
-------------------------------------
LLM calls are not reproducible, and this project lives on two runs producing
identical numbers. So the model is called ONCE, its answers are written to
results/llm_predictions.json, and every later run reads that file. Someone
cloning this repo without an API key still sees the measured accuracy and still
gets byte-identical output.

The cache is a record of an experiment that was actually run, not a substitute
for running it. Re-running it is one flag: `--infer-llm --refresh`. The prompt,
the model id, and the split are all stamped into the file so a stale cache
cannot silently be scored against a different setup.

FAIRNESS
--------
The tree gets 350 labelled invoices to fit on. Handing the LLM none would be a
rigged fight, so it gets 24 labelled examples in-context, drawn from the TRAINING
split only. The held-out 150 it is graded on are never shown to it. It sees the
same ten features in `config.FEATURE_COLUMNS` and nothing else — no
`latent_cause`, no hidden state, same leakage rail as every other arm.
"""

from __future__ import annotations

import json
import os

import numpy as np
import pandas as pd

import config as cfg

# What each cause means in business terms. This is the domain briefing a new
# analyst would get on their first day — deliberately NOT the generator's
# parameters, which would be handing over the answer key.
CAUSE_BRIEFING = """\
FORGOTTEN   - The invoice was simply missed: lost in an inbox or stuck waiting
              for an approval signature. Nobody disputes it and the money exists.
              Tends to be a customer who pays reasonably promptly in general and
              engages with email.
CASH_CRUNCH - They fully intend to pay but are temporarily short of cash. Often
              slower than average historically, and tends to settle bills late in
              the month once their own receivables land.
DISPUTE     - They believe something is wrong: wrong amount, short delivery, a
              purchase-order mismatch. Frequently a customer with a history of
              raising disputes. They engage, but they argue.
CHRONIC     - Insolvent, or systematically not paying suppliers. The strongest
              signal is silence: they do not even open the email. Usually a long
              payment history and prior write-offs against them."""


def _feature_block(row) -> str:
    """Render one invoice's observable features. FEATURE_COLUMNS only."""
    parts = []
    for col in cfg.FEATURE_COLUMNS:
        value = row[col]
        if isinstance(value, (bool, np.bool_)):
            value = "yes" if value else "no"
        elif col == "amount":
            value = f"Rs {float(value):,.0f}"
        else:
            value = f"{value}"
        parts.append(f"  {col}: {value}")
    return "\n".join(parts)


def _build_prompt(train: pd.DataFrame, seed: int = cfg.SEED) -> str:
    """The system prompt: the briefing plus labelled examples from TRAIN only."""
    examples = train.sample(n=cfg.LLM_FEWSHOT_N, random_state=seed)
    blocks = []
    for _, row in examples.iterrows():
        blocks.append(f"{_feature_block(row)}\n  ANSWER: {row['latent_cause']}")

    return (
        "You are a B2B receivables analyst. Each invoice is overdue for exactly "
        "one of four underlying reasons. You are given observable facts about the "
        "customer and the invoice, and must infer which reason applies.\n\n"
        f"THE FOUR REASONS:\n{CAUSE_BRIEFING}\n\n"
        "The clues correlate with the reason but never determine it, and roughly "
        "one invoice in ten carries genuinely misleading clues. Give your single "
        "best guess anyway.\n\n"
        f"WORKED EXAMPLES (real invoices with the true answer):\n\n"
        + "\n\n".join(blocks)
        + "\n\nAnswer with exactly one word: FORGOTTEN, CASH_CRUNCH, DISPUTE or "
          "CHRONIC. No explanation, no punctuation."
    )


def _read_key() -> str:
    """Find the API key in the environment, or in a local .env file.

    .env is gitignored. The key is never printed, never written to the cache,
    and never included in anything this repo commits.
    """
    key = os.environ.get(cfg.LLM_API_KEY_ENV, "")
    if key:
        return key

    if os.path.exists(".env"):
        with open(".env", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                name, _, value = line.partition("=")
                if name.strip() == cfg.LLM_API_KEY_ENV:
                    return value.strip().strip('"').strip("'")
    return ""


def _client():
    """Build the OpenAI-compatible client pointed at NVIDIA NIM."""
    key = _read_key()
    if not key:
        raise RuntimeError(
            f"No {cfg.LLM_API_KEY_ENV} found in the environment or in a local "
            f".env file. Create .env (it is gitignored) containing a line: "
            f"{cfg.LLM_API_KEY_ENV}=your-key-here . "
            f"The key is needed only to REFRESH the cache; scoring the existing "
            f"{cfg.LLM_PREDICTIONS_JSON} needs no key at all."
        )
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError(
            "The LLM arm needs the openai package:  pip install openai"
        ) from exc

    return OpenAI(base_url=cfg.LLM_BASE_URL, api_key=key)


def _write(path, model, n_test, test, predictions, partial=False) -> dict:
    """Write one model's cache. `partial` marks an incomplete run so it is
    never mistaken for a finished experiment."""
    payload = {
        "model": model,
        "base_url": cfg.LLM_BASE_URL,
        "temperature": cfg.LLM_TEMPERATURE,
        "fewshot_n": cfg.LLM_FEWSHOT_N,
        "extra_body": cfg.LLM_EXTRA_BODY,
        "seed": cfg.LLM_SEED,
        "n_test": n_test,
        "partial": partial,
        "test_invoice_ids": sorted(str(x) for x in test["invoice_id"]),
        "predictions": predictions,
    }
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
    return payload


def cache_path(model: str) -> str:
    """Where one model's cached answers live. One file per model, so results
    from different models can never be silently mixed."""
    slug = model.replace("/", "-").replace(".", "-")
    base, ext = os.path.splitext(cfg.LLM_PREDICTIONS_JSON)
    return f"{base}__{slug}{ext}"


def _classify_one(client, model: str, system: str, row) -> tuple:
    """One invoice, one call, with backoff on transient server errors.

    A 503 from an overloaded endpoint says nothing about the invoice, so it is
    retried rather than allowed to kill a 150-call run. This is NOT a silent
    fallback: after the retries are exhausted the error is raised, and a reply
    that names no known cause is still rejected outright rather than guessed.
    """
    import random
    import time

    last = None
    for attempt in range(cfg.LLM_MAX_RETRIES):
        try:
            return _call(client, model, system, row)
        except Exception as exc:  # noqa: BLE001 - re-raised below
            transient = any(code in str(exc) for code in ("503", "429", "500", "502", "504"))
            if not transient:
                raise
            last = exc
            # Exponential backoff with jitter, so six workers do not all
            # retry in lockstep and re-overload the endpoint.
            time.sleep(min(2 ** attempt, 16) + random.random())
    raise RuntimeError(
        f"{row['invoice_id']}: {model} failed after {cfg.LLM_MAX_RETRIES} "
        f"attempts. Last error: {last}"
    )


def _call(client, model: str, system: str, row) -> tuple:
    """The actual request. Raises on a reply that names no known cause."""
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": _feature_block(row)},
        ],
        temperature=cfg.LLM_TEMPERATURE,
        max_tokens=cfg.LLM_MAX_TOKENS,
        seed=cfg.LLM_SEED,
        # Both Nemotron models here reason by default, and will otherwise spend
        # hundreds of tokens narrating before naming a class — measured at over
        # 900 completion tokens without even finishing, against 5 tokens with
        # thinking off. There is no chain of thought worth buying for a
        # single-label classification, so it is disabled at the template level.
        extra_body=cfg.LLM_EXTRA_BODY,
    )
    raw = (response.choices[0].message.content or "").strip().upper()
    answer = next((c for c in cfg.CAUSES if c in raw), None)
    if answer is None:
        raise ValueError(
            f"{row['invoice_id']}: {model} returned {raw!r}, which names none "
            f"of {cfg.CAUSES}. Not guessing on its behalf."
        )
    return str(row["invoice_id"]), answer


def fetch_predictions(train: pd.DataFrame, test: pd.DataFrame,
                      model: str = None, path: str = None) -> dict:
    """Call one model once per held-out invoice and cache every answer.

    Requests are independent and results are keyed by invoice id, so they run in
    a small thread pool. That changes no answer — it only stops the 550B model,
    at ~9s a call, taking 23 minutes to do 150 of them.

    Fails loudly on an unparseable reply rather than guessing a label: a silent
    fallback would quietly inflate whichever class it defaulted to.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    model = model or cfg.LLM_MODEL
    path = path or cache_path(model)
    client = _client()
    system = _build_prompt(train)

    # Resume: a partial file from a run that died mid-way is picked up, and only
    # the invoices still missing are bought again. A flaky endpoint should cost
    # time, not the whole experiment.
    predictions = {}
    if os.path.exists(path):
        try:
            predictions = load_predictions(model, path).get("predictions", {})
        except (ValueError, KeyError):
            predictions = {}

    rows = [row for _, row in test.iterrows()
            if str(row["invoice_id"]) not in predictions]
    total = len(test)
    if predictions:
        print(f"  resuming: {len(predictions)} already cached, "
              f"{len(rows)} still to fetch")

    done = len(predictions)
    try:
        with ThreadPoolExecutor(max_workers=cfg.LLM_CONCURRENCY) as pool:
            futures = [pool.submit(_classify_one, client, model, system, r)
                       for r in rows]
            for future in as_completed(futures):
                invoice_id, answer = future.result()
                predictions[invoice_id] = answer
                done += 1
                if done % 25 == 0 or done == total:
                    print(f"  ...{done}/{total} classified")
    except Exception:
        # Keep whatever completed so a re-run resumes instead of restarting.
        _write(path, model, total, test, predictions, partial=True)
        print(f"  run failed with {len(predictions)}/{total} done — partial "
              f"progress saved to {path}, re-run to resume")
        raise

    return _write(path, model, total, test, predictions, partial=False)


def load_predictions(model: str = None, path: str = None) -> dict:
    """Read one model's cached run, raising loudly if it was never made."""
    model = model or cfg.LLM_MODEL
    path = path or cache_path(model)
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"{path} does not exist. Run it once with a key:\n"
            f"    python run.py --infer-llm --refresh"
        )
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def load_all_cached() -> list:
    """Every model in cfg.LLM_MODELS that has a cached run on disk."""
    found = []
    for model in cfg.LLM_MODELS:
        if os.path.exists(cache_path(model)):
            found.append(load_predictions(model))
    if not found:
        raise FileNotFoundError(
            "No cached LLM runs found. Produce them with:\n"
            "    python run.py --infer-llm --refresh"
        )
    return found


class LLMClassifier:
    """Scores exactly like the other two arms, but reads a cached experiment.

    Same interface as RulesClassifier and TreeClassifier — `name` and
    `predict(df)` — so `inference.score()` grades all three identically and no
    special-casing creeps into the comparison.
    """

    def __init__(self, payload: dict):
        self.payload = payload
        self.model = payload["model"]
        self._by_id = payload["predictions"]
        # The arm name carries the model, so a three-way table cannot be
        # misread about which model produced which row.
        self.name = f"llm {self.model.split('/')[-1]}"

    def predict(self, df: pd.DataFrame) -> np.ndarray:
        missing = [str(i) for i in df["invoice_id"] if str(i) not in self._by_id]
        if missing:
            raise ValueError(
                f"The cached LLM run does not cover {len(missing)} of these "
                f"invoices (e.g. {missing[:3]}). It was recorded against a "
                f"different split — refresh it rather than scoring a mismatch."
            )
        return np.array([self._by_id[str(i)] for i in df["invoice_id"]], dtype=object)
