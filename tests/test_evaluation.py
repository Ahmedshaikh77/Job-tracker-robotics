from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from src.evaluation import evaluate_job
from src.lifecycle import RevisionPolicy
from src.models import (
    AuthorizationStatus,
    DetailResult,
    DetailStatus,
    EmploymentType,
    FactSource,
    FreshnessStatus,
    PayPeriod,
    Recommendation,
    SalaryRange,
)
from src.profile import load_profile
from src.state import StateManager


NOW = datetime(2026, 9, 7, 20, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def profile():
    return load_profile(Path(__file__).parents[1] / "profile.yaml")


def observe_for_evaluation(tmp_path, job, *, seed=False, now=NOW):
    state = StateManager.load(tmp_path / "state.json")
    candidate_id, _, candidate = state.observe_candidate(
        job,
        now,
        discovered_during_seed=seed,
    )
    return state, candidate_id, candidate


def _salary(minimum="120000", maximum="140000"):
    return SalaryRange(
        Decimal(minimum),
        Decimal(maximum),
        "USD",
        PayPeriod.YEAR,
        FactSource.STRUCTURED_FEED,
    )


def _job(make_job, **overrides):
    values = {
        "employment_type": EmploymentType.FULL_TIME,
        "posted_at": "2026-09-05",
        "description": (
            "Minimum qualifications: 2 years of experience. Python, C++, ROS2, "
            "SolidWorks, hardware validation, and sensor integration. "
            "Visa sponsorship is available for qualified candidates."
        ),
        "salary": _salary(),
        "provenance": {
            "description": FactSource.OFFICIAL_DETAIL,
            "posted_at": FactSource.OFFICIAL_DETAIL,
            "employment_type": FactSource.STRUCTURED_FEED,
        },
    }
    values.update(overrides)
    return make_job(**values)


def _evaluate(
    tmp_path,
    profile,
    job,
    *,
    policy=None,
    now=NOW,
    seed=False,
    freshness_days=30,
    current_roundup=False,
):
    state, candidate_id, candidate = observe_for_evaluation(
        tmp_path, job, seed=seed, now=now
    )
    result = evaluate_job(
        job,
        candidate_id,
        candidate,
        profile,
        now,
        revision_policy=policy or RevisionPolicy(),
        freshness_days=freshness_days,
        current_roundup=current_roundup,
    )
    return state, candidate, result


def test_complete_apply_now_assessment_preserves_typed_outputs(
    tmp_path, profile, make_job
):
    job = _job(make_job)
    _, candidate, result = _evaluate(tmp_path, profile, job)
    assert result.candidate_id
    assert result.reopen_generation == candidate["reopen_generation"]
    assert result.eligible is True
    assert result.recommendation is Recommendation.APPLY_NOW
    assert result.score == result.score_breakdown.total
    assert result.freshness.status is FreshnessStatus.RECENT
    assert result.compensation.salary is job.salary
    assert result.qualification.points == 10
    assert result.resume_filename == "Mechatronics Engineer, Optimus Hardware Validation .pdf"
    assert result.resume_reason == "; ".join(("robotics test",))


def test_assessment_distinguishes_source_facts_from_tracker_inference(
    tmp_path, profile, make_job
):
    job = _job(
        make_job,
        salary=None,
        description=(
            "Minimum qualifications: 2 years of experience. Python and hardware "
            "validation. Must be legally authorized to work in the United States "
            "at time of hire."
        ),
    )
    _, candidate, result = _evaluate(tmp_path, profile, job)
    assert result.candidate_id
    assert result.reopen_generation == candidate["reopen_generation"]
    assert result.experience.source is FactSource.OFFICIAL_DETAIL
    assert result.authorization.status is AuthorizationStatus.OPT_COMPATIBLE_UNCERTAIN
    assert result.authorization.source is FactSource.TRACKER_INFERENCE
    assert result.resume_filename == "Mechatronics Engineer, Optimus Hardware Validation .pdf"


def test_unknown_sponsorship_can_be_moderate(tmp_path, profile, make_job):
    job = _job(
        make_job,
        salary=None,
        description="Minimum qualifications: 2 years of experience.",
    )
    _, _, result = _evaluate(tmp_path, profile, job)
    assert result.eligible is True
    assert result.recommendation is Recommendation.MODERATE
    assert result.authorization.status is AuthorizationStatus.UNKNOWN
    assert "sponsorship" in result.important_gap.lower()


@pytest.mark.parametrize(
    "restriction",
    [
        "Applicants must be U.S. persons under ITAR.",
        "Active Secret clearance required.",
        "We cannot sponsor now or in the future.",
    ],
)
def test_confirmed_authorization_restriction_is_never_alertable(
    tmp_path, profile, make_job, restriction
):
    job = _job(make_job, company="Cobot", description=f"Requirements: 2 years. {restriction}")
    _, _, result = _evaluate(tmp_path, profile, job)
    assert result.authorization.status is AuthorizationStatus.BLOCKED
    assert result.eligible is False
    assert result.recommendation is Recommendation.SKIP


def test_old_role_is_withheld(tmp_path, profile, make_job):
    job = _job(make_job, posted_at="2026-01-01")
    _, _, result = _evaluate(tmp_path, profile, job)
    assert result.freshness.status is FreshnessStatus.STALE
    assert result.eligible is False
    assert result.recommendation is Recommendation.SKIP


def test_initial_roundup_accepts_verified_seeded_opening_with_unknown_posted_date(
    tmp_path, profile, make_job
):
    job = _job(make_job, posted_at=None)
    _, _, result = _evaluate(
        tmp_path,
        profile,
        job,
        seed=True,
        current_roundup=True,
    )

    assert result.freshness.status is FreshnessStatus.CURRENT_OPENING
    assert result.freshness.eligible is True
    assert result.score_breakdown.recency == 0
    assert result.eligible is True


def test_initial_roundup_still_rejects_seeded_opening_with_known_old_date(
    tmp_path, profile, make_job
):
    job = _job(make_job, posted_at="2026-01-01")
    _, _, result = _evaluate(
        tmp_path,
        profile,
        job,
        seed=True,
        current_roundup=True,
    )

    assert result.freshness.status is FreshnessStatus.STALE
    assert result.eligible is False


def test_unresolved_full_time_is_withheld(tmp_path, profile, make_job):
    job = _job(
        make_job,
        employment_type=EmploymentType.UNKNOWN,
        provenance={
            "description": FactSource.OFFICIAL_DETAIL,
            "posted_at": FactSource.OFFICIAL_DETAIL,
        },
        description="Requirements: 2 years of experience. Python.",
    )
    _, _, result = _evaluate(tmp_path, profile, job)
    assert result.job.employment_type is EmploymentType.UNKNOWN
    assert result.eligible is False
    assert result.recommendation is Recommendation.SKIP


def test_official_detail_can_normalize_full_time_without_mutating_input(
    tmp_path, profile, make_job
):
    job = _job(
        make_job,
        employment_type=EmploymentType.UNKNOWN,
        provenance={
            "description": FactSource.OFFICIAL_DETAIL,
            "posted_at": FactSource.OFFICIAL_DETAIL,
        },
        description=(
            "This is a full-time position. Minimum qualifications: 2 years of "
            "experience. Python C++ ROS2 hardware validation sensor integration."
        ),
    )
    _, _, result = _evaluate(tmp_path, profile, job)
    assert job.employment_type is EmploymentType.UNKNOWN
    assert result.job.employment_type is EmploymentType.FULL_TIME
    assert result.job.provenance["employment_type"] is FactSource.OFFICIAL_DETAIL


def test_supported_unresolved_experience_can_remain_eligible(
    tmp_path, profile, make_job
):
    job = _job(
        make_job,
        title="Robotics Test Engineer I",
        salary=None,
        description=(
            "Responsibilities: Work within a multidisciplinary team to build and "
            "test hardware using Python, C++, ROS2, hardware validation, and sensor integration."
        ),
    )
    _, _, result = _evaluate(tmp_path, profile, job)
    assert result.experience.unresolved is True
    assert result.experience.early_career_supported is True
    assert result.eligible is True


def test_unsupported_unresolved_experience_is_withheld(tmp_path, profile, make_job):
    job = _job(
        make_job,
        salary=None,
        description="Responsibilities: Develop robotic hardware.",
    )
    _, _, result = _evaluate(tmp_path, profile, job)
    assert result.experience.unresolved is True
    assert result.experience.early_career_supported is False
    assert result.eligible is False


def test_hard_four_year_requirement_is_withheld(tmp_path, profile, make_job):
    job = _job(make_job, description="Requirements: 4+ years of experience. Python.")
    _, _, result = _evaluate(tmp_path, profile, job)
    assert result.experience.stated_required_minimum == 4
    assert result.experience.flexible is False
    assert result.eligible is False


def test_flexible_four_year_exception_is_moderate_with_gap(
    tmp_path, profile, make_job
):
    job = _job(
        make_job,
        description=(
            "Requirements: Typically 4 years of experience. Python, C++, ROS2, "
            "SolidWorks, ESP32, hardware validation, and sensor integration. "
            "Visa sponsorship is available."
        ),
    )
    _, _, result = _evaluate(tmp_path, profile, job)
    assert result.experience.flexible is True
    assert result.score - result.score_breakdown.experience_fit == 85
    assert result.eligible is True
    assert result.recommendation is Recommendation.MODERATE
    assert "four" in result.important_gap.lower()


def test_unmapped_role_and_unresolved_full_time_are_withheld(
    tmp_path, profile, make_job
):
    job = make_job(
        title="Facilities Planner",
        employment_type=EmploymentType.UNKNOWN,
        posted_at="2026-09-05",
        provenance={"posted_at": FactSource.OFFICIAL_DETAIL},
    )
    _, _, result = _evaluate(tmp_path, profile, job)
    assert result.role_family is None
    assert result.resume_filename is None
    assert result.eligible is False
    assert result.recommendation is Recommendation.SKIP


def test_missing_description_provenance_key_is_safe(tmp_path, profile, make_job):
    job = make_job(
        title="Robotics Test Engineer I",
        employment_type=EmploymentType.FULL_TIME,
        posted_at="2026-09-05",
        description="Applicants must be U.S. persons under ITAR.",
        provenance={
            "posted_at": FactSource.OFFICIAL_DETAIL,
            "employment_type": FactSource.STRUCTURED_FEED,
        },
    )
    _, _, result = _evaluate(tmp_path, profile, job)
    assert result.authorization.status is AuthorizationStatus.UNKNOWN


def test_structured_feed_restriction_text_cannot_block(tmp_path, profile, make_job):
    job = make_job(
        title="Robotics Test Engineer I",
        employment_type=EmploymentType.FULL_TIME,
        posted_at="2026-09-05",
        description="Applicants must be U.S. persons under ITAR.",
        provenance={
            "description": FactSource.STRUCTURED_FEED,
            "posted_at": FactSource.STRUCTURED_FEED,
            "employment_type": FactSource.STRUCTURED_FEED,
        },
    )
    _, _, result = _evaluate(tmp_path, profile, job)
    assert result.authorization.status is AuthorizationStatus.UNKNOWN


def test_reopen_assessment_uses_lifecycle_generation(tmp_path, profile, make_job):
    job = _job(make_job)
    state, candidate_id, _ = observe_for_evaluation(tmp_path, job)
    state.apply_detail_result(
        candidate_id,
        job.source_key,
        job.job_id,
        DetailResult(None, DetailStatus.CLOSED, NOW.isoformat()),
        NOW,
    )
    reopened_at = NOW + timedelta(days=8)
    _, _, candidate = state.observe_candidate(
        job,
        reopened_at,
        discovered_during_seed=False,
    )
    result = evaluate_job(
        job,
        candidate_id,
        candidate,
        profile,
        reopened_at,
        revision_policy=RevisionPolicy(),
    )
    assert candidate["reopen_generation"] == 1
    assert result.reopen_generation == 1


def test_evaluate_job_composes_nondefault_revision_policy(
    tmp_path, profile, make_job
):
    policy = RevisionPolicy(
        compensation_material_change_ratio=Decimal("0.25"),
        compensation_threshold=Decimal("150000"),
    )
    old = _job(make_job, posted_at="2026-01-01", salary=_salary("100000", "120000"))
    state, candidate_id, candidate = observe_for_evaluation(tmp_path, old, seed=True)
    baseline = evaluate_job(
        old,
        candidate_id,
        candidate,
        profile,
        NOW,
        revision_policy=policy,
    )
    state.record_assessment(baseline, NOW.isoformat())

    nonmaterial = _job(
        make_job,
        posted_at="2026-01-01",
        salary=_salary("120000", "130000"),
    )
    _, _, candidate = state.observe_candidate(
        nonmaterial, NOW + timedelta(hours=1), discovered_during_seed=False
    )
    unchanged = evaluate_job(
        nonmaterial,
        candidate_id,
        candidate,
        profile,
        NOW + timedelta(hours=1),
        revision_policy=policy,
    )
    assert unchanged.freshness.status is FreshnessStatus.STALE

    threshold_crossing = _job(
        make_job,
        posted_at="2026-01-01",
        salary=_salary("120000", "160000"),
    )
    _, _, candidate = state.observe_candidate(
        threshold_crossing, NOW + timedelta(hours=2), discovered_during_seed=False
    )
    changed = evaluate_job(
        threshold_crossing,
        candidate_id,
        candidate,
        profile,
        NOW + timedelta(hours=2),
        revision_policy=policy,
    )
    assert changed.freshness.status is FreshnessStatus.MATERIAL_REVISION
    assert changed.freshness.eligible is True


def test_evaluate_job_threads_configured_freshness_window(
    tmp_path, profile, make_job
):
    job = _job(make_job, posted_at="2026-08-28")
    _, _, result = _evaluate(
        tmp_path, profile, job, freshness_days=7
    )
    assert result.freshness.status is FreshnessStatus.STALE
    assert result.eligible is False
