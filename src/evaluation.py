"""Deterministic composition boundary for a fully enriched job."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime
from typing import Any

from .authorization import classify_authorization
from .compensation import assess_compensation, parse_compensation
from .eligibility import StageOneStatus, stage_one
from .experience import parse_experience
from .freshness import assess_freshness, assess_material_revision
from .lifecycle import RevisionPolicy
from .models import (
    AuthorizationStatus,
    CompensationStatus,
    EmploymentType,
    FactSource,
    Job,
    JobAssessment,
    Recommendation,
)
from .parsing import split_job_sections
from .profile import MatchProfile
from .qualifications import (
    assess_qualifications,
    extract_qualification_groups,
    match_qualification_groups,
)
from .resumes import ResumeRouter
from .scoring import score_job_v1


def _deduplicate(values: list[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value))


def evaluate_job(
    job: Job,
    candidate_id: str,
    candidate_state: Mapping[str, Any],
    profile: MatchProfile,
    now: datetime,
    *,
    revision_policy: RevisionPolicy,
    freshness_days: int = 30,
    current_roundup: bool = False,
) -> JobAssessment:
    """Evaluate one observed, detail-enriched job without mutating identity or state."""
    decision = stage_one(job)
    evaluated_job = job
    if (
        decision.employment_source
        in {FactSource.STRUCTURED_FEED, FactSource.OFFICIAL_DETAIL}
        and decision.employment_type is not job.employment_type
    ):
        evaluated_job = replace(
            job,
            employment_type=decision.employment_type,
            provenance={
                **dict(job.provenance),
                "employment_type": decision.employment_source,
            },
        )

    description_source = job.provenance.get(
        "description", FactSource.UNAVAILABLE
    )
    sections = split_job_sections(job.description)
    experience = parse_experience(
        sections,
        title=job.title,
        provenance=description_source,
    )
    authorization = classify_authorization(
        sections.full_text,
        provenance=description_source,
        candidate_authorization={
            "current_authorization": profile.candidate.current_authorization,
            "stem_opt_eligible": profile.candidate.stem_opt_eligible,
            "future_sponsorship_needed": profile.candidate.future_sponsorship_needed,
        },
    )
    parsed_compensation = parse_compensation(
        sections,
        provenance=description_source,
    )
    compensation = assess_compensation(
        structured_salary=job.salary,
        parsed=parsed_compensation,
    )
    required_groups = extract_qualification_groups(
        sections,
        profile,
        provenance=description_source,
    )
    matched_groups = match_qualification_groups(required_groups, profile)
    qualification = assess_qualifications(
        required_groups,
        matched_groups,
        preferred_experience_above_three=(
            experience.preferred_minimum is not None
            and experience.preferred_minimum > 3
        ),
        penalty_points=profile.scoring.preferred_experience_gap_penalty,
    )
    material_revision = assess_material_revision(
        evaluated_job,
        experience,
        authorization,
        compensation,
        candidate_state,
        policy=revision_policy,
    )
    freshness = assess_freshness(
        evaluated_job,
        candidate_state,
        material_revision=material_revision,
        now=now,
        freshness_days=freshness_days,
        current_roundup=current_roundup,
    )
    resume = ResumeRouter(profile.resume_routes).select(decision)

    hard_blocks: list[str] = []
    if decision.status is StageOneStatus.REJECT:
        hard_blocks.extend(decision.reasons)
    if not decision.us_location_confirmed:
        hard_blocks.append("United States location is not confirmed")
    if not decision.full_time_confirmed:
        hard_blocks.append("Full-time employment is not confirmed")
    if authorization.status is AuthorizationStatus.BLOCKED:
        hard_blocks.append("Official authorization restriction blocks the candidate")
    if experience.unresolved and not experience.early_career_supported:
        hard_blocks.append("Required experience is unresolved without early-career scope")
    if (
        experience.stated_required_minimum is not None
        and experience.stated_required_minimum > 3
        and not experience.flexible
    ):
        hard_blocks.append("Required experience exceeds three years")
    if not freshness.eligible:
        hard_blocks.append("Posting is outside the freshness policy")
    if resume is None:
        hard_blocks.append("No exact approved resume route")
    hard_block_tuple = _deduplicate(hard_blocks)

    scored = score_job_v1(
        job=evaluated_job,
        stage_one=decision,
        experience=experience,
        authorization=authorization,
        compensation=compensation,
        freshness=freshness,
        qualification=qualification,
        profile=profile,
        hard_blocks=hard_block_tuple,
    )

    gap = ""
    if qualification.missing_required_groups:
        gap = f"Missing required qualification: {qualification.missing_required_groups[0]}"
    elif (
        experience.stated_required_minimum is not None
        and experience.stated_required_minimum > 3
        and experience.flexible
    ):
        gap = "Published requirement states a four-year experience gap"
    elif authorization.status in {
        AuthorizationStatus.UNKNOWN,
        AuthorizationStatus.OPT_COMPATIBLE_UNCERTAIN,
    }:
        gap = "Future sponsorship support is uncertain"
    elif compensation.status is CompensationStatus.UNPUBLISHED:
        gap = "Base compensation is not published"
    elif compensation.status is CompensationStatus.UNRESOLVED:
        gap = "Published base compensation is unresolved"
    elif compensation.status is CompensationStatus.BELOW_TARGET:
        gap = "Published base compensation is below $100k"
    elif hard_block_tuple:
        gap = hard_block_tuple[0]
    else:
        gap = scored.important_gap

    eligible = not hard_block_tuple and scored.recommendation is not Recommendation.SKIP
    return JobAssessment(
        job=evaluated_job,
        candidate_id=candidate_id,
        reopen_generation=int(candidate_state["reopen_generation"]),
        eligible=eligible,
        score=scored.score,
        recommendation=scored.recommendation if eligible else Recommendation.SKIP,
        role_family=decision.role_family,
        match_reason=scored.match_reason,
        important_gap=gap,
        resume_filename=resume.filename if resume else None,
        resume_reason=resume.reason if resume else "",
        experience=experience,
        authorization=authorization,
        freshness=freshness,
        compensation=compensation,
        qualification=qualification,
        score_breakdown=scored.breakdown,
        hard_blocks=hard_block_tuple,
    )
