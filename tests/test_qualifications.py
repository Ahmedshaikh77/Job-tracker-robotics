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
    qualification_gap,
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


def test_degree_matching_uses_loaded_candidate_evidence_not_a_hidden_constant(
    tmp_path
):
    from tests.test_profile import _write_profile, valid_profile_dict

    payload = valid_profile_dict()
    payload["candidate"]["degree"] = "MS Electrical Engineering, Test University"
    changed_profile = load_profile(_write_profile(tmp_path, payload))
    assert match_qualification_groups(
        ("degree:mechanical engineering",), changed_profile
    ) == ()
    assert match_qualification_groups(
        ("degree:electrical engineering",), changed_profile
    ) == ("degree:electrical engineering",)


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


def _assess_required_text(text, profile):
    groups = extract_qualification_groups(
        split_job_sections(f"Minimum Qualifications: {text}"),
        profile,
        provenance=FactSource.OFFICIAL_DETAIL,
    )
    return assess_qualifications(
        groups,
        match_qualification_groups(groups, profile),
        preferred_experience_above_three=False,
    )


@pytest.mark.parametrize(
    "text",
    [
        "Bachelor's degree in Computer Science or other technical degree or related experience",
        "Bachelor's degree in engineering.",
        "Bachelor's degree in Computer Science, engineering, or mathematics.",
        "An engineering degree is required.",
    ],
)
def test_explicit_broad_degree_accepts_confirmed_mechanical_engineering(text, profile):
    assessment = _assess_required_text(text, profile)
    assert len(assessment.required_groups) == 1
    assert assessment.matched_required_groups == assessment.required_groups
    assert assessment.missing_required_groups == ()
    assert assessment.points == 10


@pytest.mark.parametrize(
    "text",
    [
        "Bachelor’s or Master’s degree in computer science, electrical engineering, or equivalent experience",
        "Bachelor's or Master's degree in Electrical and Computer Engineering, Robotics, Computer Science or equivalent experience",
        "Bachelor's degree in Computer Science or a related field.",
        "Bachelor's degree in Computer Science or related experience.",
    ],
)
def test_unconfirmed_degree_alternative_needs_review_without_awarding_credit(text, profile):
    assessment = _assess_required_text(text, profile)
    assert len(assessment.required_groups) == 1
    assert assessment.matched_required_groups == ()
    assert assessment.missing_required_groups == ()
    assert assessment.points == 0


@pytest.mark.parametrize(
    "text",
    [
        "Bachelor's degree in Computer Science is required.",
        "Bachelor's degree in Computer Science; Python or equivalent experience is required.",
        "Bachelor's degree in Computer Science; equivalent experience is not accepted.",
        "Bachelor's degree in Computer Science or equivalent experience is not accepted.",
        "Bachelor's degree in Computer Science or engineering management.",
        "Bachelor's degree in Computer Science or engineering physics.",
        "Bachelor's degree in Computer Science or engineering experience.",
    ],
)
def test_strict_degree_and_negated_or_unrelated_alternative_stay_missing(text, profile):
    assessment = _assess_required_text(text, profile)
    assert assessment.missing_required_groups == ("degree:computer science",)
    assert "degree:computer science" not in assessment.matched_required_groups


def test_degree_review_preserves_skill_and_domain_coverage(profile):
    assessment = _assess_required_text(
        "Degree in Computer Science or equivalent experience. Python and hardware validation.",
        profile,
    )
    assert assessment.matched_required_groups == ("skill:Python", "domain:hardware validation")
    assert assessment.points == 7
    assert assessment.missing_required_groups == ()


def test_broad_technical_degree_does_not_invent_engineering_evidence(tmp_path):
    from tests.test_profile import _write_profile, valid_profile_dict

    payload = valid_profile_dict()
    payload["candidate"]["degree"] = "BA History, Test University"
    changed_profile = load_profile(_write_profile(tmp_path, payload))
    assessment = _assess_required_text(
        "Bachelor's degree in Computer Science or other technical degree or related experience",
        changed_profile,
    )
    assert len(assessment.required_groups) == 1
    assert assessment.matched_required_groups == ()
    assert assessment.points == 0


def test_explicit_degree_match_does_not_require_review_of_experience_alternative(profile):
    assessment = _assess_required_text(
        "Degree in Mechanical Engineering or equivalent experience.", profile
    )
    assert assessment.matched_required_groups == assessment.required_groups
    assert assessment.missing_required_groups == ()
    assert assessment.points == 10


@pytest.mark.parametrize("group, label", [("skill:PLC", "PLC"), ("domain:fluid dynamics", "fluid dynamics")])
def test_missing_skill_and_domain_warning_uses_readable_label(group, label):
    assessment = assess_qualifications(
        (group,), (), preferred_experience_above_three=False
    )
    gap = qualification_gap(assessment)
    assert label in gap
    assert "Missing required qualification" in gap
    assert "skill:" not in gap
    assert "domain:" not in gap


@pytest.mark.parametrize(
    "text, review_path",
    [
        ("Degree in Computer Science or equivalent experience, no prior industry experience required.", "equivalent experience"),
        ("Degree in Computer Science or equivalent practical experience.", "equivalent practical experience"),
        ("Degree in Computer Science or a related technical field.", "related technical field"),
    ],
)
def test_degree_alternative_retains_its_own_context(text, review_path, profile):
    assessment = _assess_required_text(text, profile)
    assert assessment.missing_required_groups == ()
    assert assessment.matched_required_groups == ()
    assert assessment.points == 0
    gap = qualification_gap(assessment)
    assert "Review degree" in gap
    assert review_path in gap


def test_flattened_separate_skill_alternative_does_not_relax_degree(profile):
    assessment = _assess_required_text(
        "Degree in Computer Science\nPython or equivalent experience", profile
    )
    assert assessment.missing_required_groups == ("degree:computer science",)
    assert assessment.matched_required_groups == ("skill:Python",)
    assert assessment.points == 5


@pytest.mark.parametrize("negation", ["is not accepted", "will not be accepted", "isn't accepted"])
def test_negation_attached_to_equivalent_experience_remains_strict(negation, profile):
    assessment = _assess_required_text(
        f"Degree in Computer Science or equivalent experience {negation}.", profile
    )
    assert assessment.missing_required_groups == ("degree:computer science",)
    assert assessment.points == 0
