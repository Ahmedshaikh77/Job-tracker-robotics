"""Exact role-family to resume-label routing."""

from __future__ import annotations

from dataclasses import dataclass

from .eligibility import StageOneDecision, StageOneStatus
from .profile import ResumeRoute


@dataclass(frozen=True, slots=True)
class ResumeRecommendation:
    filename: str
    role_family: str
    evidence: tuple[str, ...]

    @property
    def reason(self) -> str:
        return "; ".join(self.evidence)


class ResumeRouter:
    """Select from the validated ordered route inventory."""

    def __init__(self, routes: tuple[ResumeRoute, ...]):
        self._routes = routes

    def select(self, decision: StageOneDecision) -> ResumeRecommendation | None:
        if (
            decision.status is StageOneStatus.REJECT
            or decision.role_family is None
        ):
            return None
        for route in self._routes:
            if route.family == decision.role_family:
                return ResumeRecommendation(
                    filename=route.filename,
                    role_family=route.family,
                    evidence=decision.role_evidence,
                )
        return None
