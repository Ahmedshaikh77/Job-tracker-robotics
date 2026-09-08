"""Version-one evidence-based matching score."""

from __future__ import annotations

import re

from .eligibility import StageOneDecision, StageOneStatus
from .models import (
    AuthorizationAssessment,
    AuthorizationStatus,
    CompensationAssessment,
    ExperienceRequirement,
    FactSource,
    FreshnessAssessment,
    FreshnessStatus,
    Job,
    QualificationAssessment,
    Recommendation,
    ScoreBreakdown,
    ScoringResult,
)
from .profile import MatchProfile


def experience_points(required_minimum: float | None, *, unresolved: bool) -> int:
    if unresolved:
        return 10
    if required_minimum is None:
        return 0
    if required_minimum <= 1:
        return 15
    if required_minimum <= 2:
        return 14
    if required_minimum <= 3:
        return 12
    return 0


def recommendation_for(score: int) -> Recommendation:
    if score >= 85:
        return Recommendation.APPLY_NOW
    if score >= 75:
        return Recommendation.STRONG
    if score >= 55:
        return Recommendation.MODERATE
    return Recommendation.SKIP


def _normalise(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().casefold()


def _contains(text: str, phrase: str) -> bool:
    return bool(
        re.search(
            rf"(?<![a-z0-9]){re.escape(_normalise(phrase))}(?![a-z0-9])",
            _normalise(text),
        )
    )


def _role_points(decision: StageOneDecision) -> int:
    signals = tuple(dict.fromkeys(decision.matched_title_signals))
    if any(len(_normalise(signal).split()) >= 2 for signal in signals):
        return 25
    if len(signals) >= 2:
        return 20
    if len(signals) == 1:
        return 15
    return 0


def _evidence_text(job: Job) -> str:
    if job.provenance.get("description", FactSource.UNAVAILABLE) is FactSource.OFFICIAL_DETAIL:
        return f"{job.title} {job.description}"
    return job.title


def _technical_matches(job: Job, profile: MatchProfile) -> tuple[str, ...]:
    text = _evidence_text(job)
    return tuple(
        skill
        for skill, aliases in profile.skill_aliases.items()
        if any(_contains(text, alias) for alias in aliases)
    )


def _domain_matches(job: Job, profile: MatchProfile) -> tuple[str, ...]:
    text = _evidence_text(job)
    return tuple(domain for domain in profile.domains if _contains(text, domain))


def _authorization_points(assessment: AuthorizationAssessment) -> int:
    return {
        AuthorizationStatus.CONFIRMED_SUPPORT: 10,
        AuthorizationStatus.OPT_COMPATIBLE_UNCERTAIN: 7,
        AuthorizationStatus.UNKNOWN: 4,
        AuthorizationStatus.BLOCKED: 0,
    }[assessment.status]


def _recency_points(assessment: FreshnessAssessment) -> int:
    if assessment.status is FreshnessStatus.UNKNOWN_DATE_POST_SEED:
        return 2
    if assessment.status is FreshnessStatus.MATERIAL_REVISION:
        return 5
    if assessment.status is not FreshnessStatus.RECENT or assessment.age_days is None:
        return 0
    if assessment.age_days <= 7:
        return 5
    if assessment.age_days <= 14:
        return 3
    if assessment.age_days <= 30:
        return 1
    return 0


def score_job_v1(
    *,
    job: Job,
    stage_one: StageOneDecision,
    experience: ExperienceRequirement,
    authorization: AuthorizationAssessment,
    compensation: CompensationAssessment,
    freshness: FreshnessAssessment,
    qualification: QualificationAssessment,
    profile: MatchProfile,
    hard_blocks: tuple[str, ...] = (),
) -> ScoringResult:
    """Produce a capped score and recommendation from verified evidence."""
    skill_matches = _technical_matches(job, profile)
    domain_matches = _domain_matches(job, profile)
    caps = profile.scoring.caps
    breakdown = ScoreBreakdown(
        scoring_version=profile.scoring.version,
        role_alignment=min(caps.role_alignment, _role_points(stage_one)),
        technical_evidence=min(caps.technical_evidence, 3 * len(skill_matches)),
        experience_fit=min(
            caps.experience_fit,
            experience_points(
                experience.effective_required_minimum,
                unresolved=experience.unresolved,
            ),
        ),
        domain_alignment=min(caps.domain_alignment, 5 * len(domain_matches)),
        qualification_coverage=min(caps.qualification_coverage, qualification.points),
        authorization=min(caps.authorization, _authorization_points(authorization)),
        compensation=min(caps.compensation, compensation.points),
        recency=min(caps.recency, _recency_points(freshness)),
    )
    automatic_blocks = list(hard_blocks)
    if stage_one.status is StageOneStatus.REJECT:
        automatic_blocks.append("stage one rejected")
    if authorization.status is AuthorizationStatus.BLOCKED:
        automatic_blocks.append("authorization blocked")
    if not freshness.eligible:
        automatic_blocks.append("stale job")
    if experience.unresolved and not experience.early_career_supported:
        automatic_blocks.append("required experience unresolved")
    if (
        experience.stated_required_minimum is not None
        and experience.stated_required_minimum > 3
        and not experience.flexible
    ):
        automatic_blocks.append("required experience above three years")

    recommendation = recommendation_for(breakdown.total)
    important_gap = ""
    flexible_four = (
        experience.stated_required_minimum is not None
        and experience.stated_required_minimum > 3
        and experience.flexible
    )
    if flexible_four:
        nonexperience = breakdown.total - breakdown.experience_fit
        important_gap = "Published requirement states a four-year experience gap"
        if nonexperience < 85:
            automatic_blocks.append("flexible four-year exception requires 85 non-experience points")
        elif recommendation is not Recommendation.SKIP:
            recommendation = Recommendation.MODERATE
    if automatic_blocks:
        recommendation = Recommendation.SKIP
        if not important_gap:
            important_gap = automatic_blocks[0]
    elif qualification.missing_required_groups:
        important_gap = qualification.missing_required_groups[0]
    elif qualification.preferred_gaps:
        important_gap = qualification.preferred_gaps[0]

    evidence = list(dict.fromkeys(stage_one.role_evidence))
    evidence.extend(f"skill: {value}" for value in skill_matches)
    evidence.extend(f"domain: {value}" for value in domain_matches)
    evidence.extend(qualification.matched_required_groups)
    match_reason = "; ".join(dict.fromkeys(evidence))
    return ScoringResult(
        breakdown=breakdown,
        recommendation=recommendation,
        match_reason="; ".join(match_reason.split("; ")[:3]),
        important_gap=important_gap,
    )
