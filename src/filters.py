"""Temporary compatibility facade for the typed Stage-1 eligibility gate."""
from __future__ import annotations

from typing import Any

from .eligibility import StageOneStatus, stage_one
from .models import Job


class JobFilter:
    """Bridge old callers to :func:`stage_one` until their planned cutover."""

    def __init__(self, cfg: dict[str, Any] | None = None):
        self.cfg = cfg or {}

    def score(self, job: Job) -> tuple[int, dict[str, Any]]:
        decision = stage_one(job)
        passed = decision.status is StageOneStatus.ENRICH
        return int(passed), {
            "stage_one_status": decision.status.value,
            "role_family": decision.role_family,
            "role_evidence": list(decision.role_evidence),
            "reasons": list(decision.reasons),
            "location_ok": decision.us_location_confirmed,
            "full_time_confirmed": decision.full_time_confirmed,
        }

    def passes(self, job: Job) -> tuple[bool, int, dict[str, Any]]:
        score, bd = self.score(job)
        return bool(score), score, bd
