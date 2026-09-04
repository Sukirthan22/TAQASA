"""
audit.py — the append-only decision log. One JSONL row per decision, with a reason.

Plain English: a written record of every decision the agent made, why it made
it, and whether a guardrail stopped it. One line of JSON per decision, added to
the end of the file and never edited afterwards.

WHY APPEND-ONLY MATTERS
-----------------------
A log you can rewrite is not evidence, it is a draft. In a real collections
system this file is what you hand to a regulator, an auditor, or a customer who
asks "why did you keep emailing me?" — and its value comes entirely from the
fact that nobody can go back and tidy up the embarrassing rows.

So this module offers exactly one mutation: `write`, which appends. There is no
update, no delete, no rewrite. Starting a fresh run truncates the file ONCE, at
construction, deliberately and visibly — and after that the handle only ever
appends.

WHAT A ROW LOOKS LIKE (PRD Phase 4)
-----------------------------------
    {"invoice_id": "INV-0042", "day": 61, "inferred_cause": "CHRONIC",
     "confidence": 0.8, "action": "WAIT", "reason": "VETOED ESCALATE_LEGAL (...)",
     "guardrail_triggered": "C2.5_legal_not_eligible:amount_150000_overdue_61",
     "cost": 0.0, "cash": 0.0}

`guardrail_triggered` is null on a row where the agent got what it asked for,
and names the specific PRD C2 rule on a row where it did not.
"""

from __future__ import annotations

import json
import os

import config as cfg

# The fields written, in this order, on every row. Fixed so the file is
# diffable and so a missing field is a loud KeyError rather than a quiet gap.
FIELDS = (
    "invoice_id",
    "day",
    "inferred_cause",
    "confidence",
    "action",
    "reason",
    "guardrail_triggered",
    "world_rail",
    "cost",
    "cash",
    "events",
)


class AuditLog:
    """An append-only JSONL writer."""

    def __init__(self, path: str = cfg.AUDIT_LOG_JSONL):
        self.path = path
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        # The one and only truncation, at the start of a run. Everything after
        # this point is strictly an append.
        with open(path, "w", encoding="utf-8"):
            pass
        self.rows_written = 0

    def write(self, record: dict) -> None:
        """Append one decision. Raises if the record is missing a field."""
        row = {field: record[field] for field in FIELDS}
        with open(self.path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, default=str) + "\n")
        self.rows_written += 1

    def write_all(self, records) -> int:
        """Append a whole run's worth of decisions, in order."""
        for record in records:
            self.write(record)
        return self.rows_written


def write_run(result, path: str = cfg.AUDIT_LOG_JSONL) -> str:
    """Write every decision from a completed policy run to disk."""
    log = AuditLog(path)
    log.write_all(result.decisions)
    return path


def summarise(path: str = cfg.AUDIT_LOG_JSONL) -> dict:
    """Read the log back and count what is in it. Used for the Gate 4 proof."""
    if not os.path.exists(path):
        raise FileNotFoundError(f"{path} does not exist — run the agent first.")

    rows = 0
    vetoes = {}
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            rows += 1
            rule = json.loads(line).get("guardrail_triggered")
            if rule:
                # Group by rule id, discarding the per-invoice detail after it.
                key = rule.split(":")[0]
                vetoes[key] = vetoes.get(key, 0) + 1

    return {"rows": rows, "vetoes": vetoes, "veto_total": sum(vetoes.values())}
