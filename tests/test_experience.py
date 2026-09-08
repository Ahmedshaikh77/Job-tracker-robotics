from __future__ import annotations

import pytest

from src.experience import parse_experience
from src.models import FactSource
from src.parsing import split_job_sections


def _parse(text: str, title: str = "Robotics Test Engineer"):
    return parse_experience(
        split_job_sections(text),
        title=title,
        provenance=FactSource.OFFICIAL_DETAIL,
    )


@pytest.mark.parametrize(
    ("text", "required_minimum", "required_maximum", "flexible"),
    [
        ("Minimum qualifications: 0-2 years of experience.", 0, 2, False),
        ("Minimum qualifications: 3+ years of experience.", 3, None, False),
        ("Minimum qualifications: 4+ years of experience.", 4, None, False),
        ("Minimum qualifications: 3-5 years of experience.", 3, 5, True),
        ("Requirements: Typically 4 years of experience.", 4, None, True),
    ],
)
def test_required_experience_preserves_bounds_and_flexibility(
    text, required_minimum, required_maximum, flexible
):
    result = _parse(text)
    assert result.stated_required_minimum == required_minimum
    assert result.stated_required_maximum == required_maximum
    assert result.flexible is flexible


def test_preferred_years_do_not_become_required_years():
    result = _parse("Preferred qualifications: 5+ years of experience.")
    assert result.stated_required_minimum is None
    assert result.preferred_minimum == 5


def test_strictest_numeric_minimum_wins_across_required_qualifications():
    result = _parse(
        "Basic Qualifications: 2 years of Python. "
        "5+ years of engineering experience."
    )
    assert result.stated_required_minimum == 5
    assert result.stated_required_maximum is None
    assert result.evidence == "5+ years of engineering experience."
    assert result.unresolved is False


def test_parser_ignores_dates_voltages_and_product_versions():
    result = _parse("Launched in 2026. Validate 24 V hardware running ROS 2.")
    assert result.stated_required_minimum is None
    assert result.preferred_minimum is None


def test_degree_substitution_reduces_effective_requirement():
    result = _parse(
        "Minimum qualifications: 4 years with a bachelor's degree, "
        "or 2 years with a master's degree."
    )
    assert result.stated_required_minimum == 4
    assert result.effective_required_minimum == 2
    assert result.flexible is True
    assert "master" in result.flexibility_basis.lower()


@pytest.mark.parametrize(
    "title",
    [
        "Robotics Test Engineer I",
        "Robotics Test Engineer II",
        "Junior Robotics Test Engineer",
        "Associate Robotics Test Engineer",
        "Entry Level Robotics Test Engineer",
        "Early Career Robotics Test Engineer",
        "Robotics Test Engineer, New Graduate",
    ],
)
def test_early_career_title_supports_unresolved_experience(title):
    result = _parse("Responsibilities: Build and test robotic hardware.", title)
    assert result.unresolved is True
    assert result.early_career_supported is True


def test_team_scoped_hands_on_responsibilities_support_unlevelled_role():
    result = _parse(
        "Responsibilities: Work within a multidisciplinary team to build, "
        "test, and document robotic hardware.",
        "Robotics Test Engineer",
    )
    assert result.unresolved is True
    assert result.early_career_supported is True


@pytest.mark.parametrize(
    "responsibility",
    [
        "Own the system architecture and technical direction.",
        "Lead the roadmap and mentor junior engineers.",
        "Hire and manage the validation team.",
        "Develop robotic hardware.",
    ],
)
def test_ambiguous_or_leadership_scope_does_not_support_unresolved_role(
    responsibility,
):
    result = _parse(f"Responsibilities: {responsibility}", "Robotics Test Engineer")
    assert result.unresolved is True
    assert result.early_career_supported is False


def test_nonofficial_text_cannot_establish_required_years():
    result = parse_experience(
        split_job_sections("Minimum qualifications: 2 years of experience."),
        title="Robotics Test Engineer I",
        provenance=FactSource.STRUCTURED_FEED,
    )
    assert result.stated_required_minimum is None
    assert result.unresolved is True
