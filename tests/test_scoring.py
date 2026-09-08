from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from src.eligibility import stage_one
from src.models import (
    AuthorizationAssessment,
    AuthorizationStatus,
    CompensationAssessment,
    CompensationStatus,
    ExperienceRequirement,
    FactSource,
    FreshnessAssessment,
    FreshnessStatus,
    PayPeriod,
    QualificationAssessment,
    Recommendation,
    SalaryRange,
)
from src.profile import load_profile
from src.scoring import experience_points, recommendation_for, score_job_v1


@pytest.fixture(scope="module")
def profile():
    return load_profile(Path(__file__).parents[1] / "profile.yaml")


def experience(required_minimum=2, *, unresolved=False, flexible=False):
    return ExperienceRequirement(
        stated_required_minimum=required_minimum,
        stated_required_maximum=None,
        effective_required_minimum=required_minimum,
        effective_required_maximum=None,
        preferred_minimum=None,
        preferred_maximum=None,
        flexible=flexible,
        flexibility_basis="typical" if flexible else "",
        unresolved=unresolved,
        early_career_supported=unresolved,
        evidence="experience evidence",
        source=FactSource.OFFICIAL_DETAIL,
    )


def authorization(status=AuthorizationStatus.UNKNOWN):
    source = (
        FactSource.TRACKER_INFERENCE
        if status is AuthorizationStatus.OPT_COMPATIBLE_UNCERTAIN
        else FactSource.OFFICIAL_DETAIL
    )
    return AuthorizationAssessment(status, status.value, source)


def compensation(points=3):
    if points == 10:
        salary = SalaryRange(
            Decimal("120000"),
            Decimal("140000"),
            "USD",
            PayPeriod.YEAR,
            FactSource.STRUCTURED_FEED,
        )
        return CompensationAssessment(
            CompensationStatus.CONFIRMED_TARGET,
            "confirmed $100k+",
            10,
            salary,
            "$120,000-$140,000 per year",
            FactSource.STRUCTURED_FEED,
        )
    return CompensationAssessment(
        CompensationStatus.UNPUBLISHED,
        "not published",
        points,
        None,
        "No base compensation published",
        FactSource.UNAVAILABLE,
    )


def freshness(age_days=2):
    return FreshnessAssessment(
        FreshnessStatus.RECENT,
        True,
        age_days,
        "posted recently",
        FactSource.OFFICIAL_DETAIL,
    )


def qualification(points=5):
    required = ("skill:Python",) if points != 5 else ()
    matched = required if points == 10 else ()
    return QualificationAssessment(
        required,
        matched,
        tuple(group for group in required if group not in matched),
        (),
        points,
    )


@pytest.mark.parametrize(
    ("required_minimum", "unresolved", "points"),
    [(0, False, 15), (1, False, 15), (2, False, 14), (3, False, 12), (None, True, 10)],
)
def test_experience_points_follow_version_one_table(
    required_minimum, unresolved, points
):
    assert experience_points(required_minimum, unresolved=unresolved) == points


@pytest.mark.parametrize(
    ("score", "recommendation"),
    [
        (100, Recommendation.APPLY_NOW),
        (85, Recommendation.APPLY_NOW),
        (84, Recommendation.STRONG),
        (75, Recommendation.STRONG),
        (74, Recommendation.MODERATE),
        (55, Recommendation.MODERATE),
        (54, Recommendation.SKIP),
    ],
)
def test_recommendation_boundaries_are_inclusive(score, recommendation):
    assert recommendation_for(score) is recommendation


@pytest.mark.parametrize(
    ("description", "expected_points"),
    [
        ("Use camera systems in mechanical assemblies.", 3),
        ("Use camera systems and C++.", 6),
        ("Use Python, python, and PYTHON.", 3),
        ("Develop with ROS2.", 3),
    ],
)
def test_technical_evidence_uses_alias_boundaries_and_distinct_groups(
    description, expected_points, make_job, profile
):
    job = make_job(
        description=description,
        provenance={"description": FactSource.OFFICIAL_DETAIL},
    )
    result = score_job_v1(
        job=job,
        stage_one=stage_one(job),
        experience=experience(),
        authorization=authorization(),
        compensation=compensation(),
        freshness=freshness(),
        qualification=qualification(),
        profile=profile,
    )
    assert result.breakdown.technical_evidence == expected_points


def test_untagged_description_cannot_add_skill_or_domain_evidence(make_job, profile):
    job = make_job(
        description="Python C++ ROS2 hardware validation sensor integration",
        provenance={},
    )
    result = score_job_v1(
        job=job,
        stage_one=stage_one(job),
        experience=experience(),
        authorization=authorization(),
        compensation=compensation(),
        freshness=freshness(),
        qualification=qualification(),
        profile=profile,
    )
    assert result.breakdown.technical_evidence == 0
    assert result.breakdown.domain_alignment == 0


def test_domain_and_repeated_role_evidence_respect_caps(make_job, profile):
    job = make_job(
        title="Robotics Test and Robot Validation Engineer",
        description="Hardware validation, sensor integration, and failure analysis.",
        provenance={"description": FactSource.OFFICIAL_DETAIL},
    )
    result = score_job_v1(
        job=job,
        stage_one=stage_one(job),
        experience=experience(),
        authorization=authorization(),
        compensation=compensation(),
        freshness=freshness(),
        qualification=qualification(),
        profile=profile,
    )
    assert result.breakdown.role_alignment == 25
    assert result.breakdown.domain_alignment == 10


def test_hard_block_always_returns_skip(make_job, profile):
    job = make_job()
    result = score_job_v1(
        job=job,
        stage_one=stage_one(job),
        experience=experience(),
        authorization=authorization(),
        compensation=compensation(10),
        freshness=freshness(),
        qualification=qualification(10),
        profile=profile,
        hard_blocks=("authorization blocked",),
    )
    assert result.recommendation is Recommendation.SKIP


def test_flexible_four_year_role_requires_perfect_nonexperience_subtotal(
    make_job, profile
):
    job = make_job(
        description=(
            "Python C++ ROS2 SolidWorks ESP32 hardware validation sensor integration"
        ),
        provenance={"description": FactSource.OFFICIAL_DETAIL},
    )
    result = score_job_v1(
        job=job,
        stage_one=stage_one(job),
        experience=experience(4, flexible=True),
        authorization=authorization(AuthorizationStatus.CONFIRMED_SUPPORT),
        compensation=compensation(10),
        freshness=freshness(),
        qualification=qualification(10),
        profile=profile,
    )
    nonexperience = result.score - result.breakdown.experience_fit
    assert nonexperience == 85
    assert result.recommendation is Recommendation.MODERATE
    assert "four" in result.important_gap.lower()


def test_flexible_four_year_role_below_perfect_subtotal_is_skipped(
    make_job, profile
):
    job = make_job(
        description="Python C++ ROS2 SolidWorks hardware validation sensor integration",
        provenance={"description": FactSource.OFFICIAL_DETAIL},
    )
    result = score_job_v1(
        job=job,
        stage_one=stage_one(job),
        experience=experience(4, flexible=True),
        authorization=authorization(AuthorizationStatus.CONFIRMED_SUPPORT),
        compensation=compensation(10),
        freshness=freshness(),
        qualification=qualification(10),
        profile=profile,
    )
    assert result.score - result.breakdown.experience_fit < 85
    assert result.recommendation is Recommendation.SKIP


def test_score_breakdown_never_exceeds_one_hundred(make_job, profile):
    job = make_job(
        description=(
            "Python C++ MATLAB ROS2 MoveIt2 Linux Git ESP32 Jetson SolidWorks "
            "Fusion 360 RealSense IMU load cell encoder camera motor actuator "
            "hardware validation sensor integration failure analysis"
        ),
        provenance={"description": FactSource.OFFICIAL_DETAIL},
    )
    result = score_job_v1(
        job=job,
        stage_one=stage_one(job),
        experience=experience(0),
        authorization=authorization(AuthorizationStatus.CONFIRMED_SUPPORT),
        compensation=compensation(10),
        freshness=freshness(0),
        qualification=qualification(10),
        profile=profile,
    )
    assert result.score == 100
