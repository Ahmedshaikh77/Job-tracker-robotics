from __future__ import annotations

from pathlib import Path

import pytest

from src.models import FactSource
from src.parsing import split_job_sections
from src.profile import load_profile
from src.qualifications import (
    assess_qualifications,
    extract_qualification_groups,
    match_qualification_groups,
)


@pytest.fixture(scope="module")
def profile():
    return load_profile(Path(__file__).parents[1] / "profile.yaml")


def test_extracts_distinct_required_skill_domain_and_degree_groups(profile):
    sections = split_job_sections(
        """
        Minimum Qualifications
        Bachelor's degree in Mechanical Engineering or Materials Science.
        Experience with Python, C++, and hardware validation.
        Must be authorized to work in the United States.
        2 years of experience.
        Preferred Qualifications
        ROS 2 and 5 years of experience.
        """
    )
    groups = extract_qualification_groups(
        sections, profile, provenance=FactSource.OFFICIAL_DETAIL
    )
    assert groups == (
        "degree:mechanical engineering|materials science",
        "skill:Python",
        "skill:C++",
        "domain:hardware validation",
    )


def test_nonofficial_required_text_extracts_no_groups(profile):
    sections = split_job_sections("Requirements: Python and hardware validation.")
    assert extract_qualification_groups(
        sections, profile, provenance=FactSource.STRUCTURED_FEED
    ) == ()


def test_no_structured_requirements_defaults_to_half_credit(profile):
    sections = split_job_sections("Build useful robotic systems.")
    groups = extract_qualification_groups(
        sections, profile, provenance=FactSource.OFFICIAL_DETAIL
    )
    assessment = assess_qualifications(
        groups,
        match_qualification_groups(groups, profile),
        preferred_experience_above_three=False,
    )
    assert groups == ()
    assert assessment.points == 5


def test_match_groups_uses_explicit_profile_evidence_and_deduplicates(profile):
    groups = (
        "skill:Python",
        "skill:Python",
        "skill:Rust",
        "domain:sensor integration",
    )
    assert match_qualification_groups(groups, profile) == (
        "skill:Python",
        "domain:sensor integration",
    )


@pytest.mark.parametrize(
    "group",
    [
        "degree:mechanical engineering",
        "degree:materials science",
        "degree:mechanical engineering|electrical engineering",
    ],
)
def test_mechanical_materials_masters_satisfies_related_degree(group, profile):
    assert match_qualification_groups((group,), profile) == (group,)


@pytest.mark.parametrize(
    "group",
    [
        "degree:electrical engineering",
        "degree:computer science",
        "degree:civil engineering",
    ],
)
def test_degree_does_not_match_unrelated_field(group, profile):
    assert match_qualification_groups((group,), profile) == ()


def test_qualification_coverage_uses_literal_required_ratio():
    assessment = assess_qualifications(
        ("skill:Python", "skill:C++", "skill:Rust"),
        ("skill:Python", "skill:C++"),
        preferred_experience_above_three=False,
    )
    assert assessment.points == 7
    assert assessment.missing_required_groups == ("skill:Rust",)


def test_preferred_experience_gap_subtracts_configured_penalty():
    assessment = assess_qualifications(
        ("skill:Python",),
        ("skill:Python",),
        preferred_experience_above_three=True,
        penalty_points=2,
    )
    assert assessment.points == 8
    assert assessment.preferred_gaps == ("preferred experience above three years",)
