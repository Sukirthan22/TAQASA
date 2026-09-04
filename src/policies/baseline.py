"""
baseline.py — the dumb opponent: a fixed dunning ladder, identical for everyone.

NUDGE_SOFT on day 7, NUDGE_FIRM on day 21, CALL on day 45. No inference, no
judgement, no stopping. This is what most receivables teams actually run today,
and it is the number the agent has to beat.

PHASE 2. Not built yet.
"""

from __future__ import annotations


def decide(*args, **kwargs):
    raise NotImplementedError("baseline.decide is Phase 2. Finish Gate 1 first.")
