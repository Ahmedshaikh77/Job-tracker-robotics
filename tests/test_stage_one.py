from __future__ import annotations

import pytest

from src.eligibility import (
    StageOneStatus,
    classify_role_family,
    is_us_location,
    stage_one,
)
from src.models import EmploymentType, FactSource


@pytest.mark.parametrize(
    ("title", "expected_family"),
    [
        ("Robotics Systems Engineer", "robotics_systems_integration"),
        ("Robotics Test Engineer", "robotics_test_validation"),
        ("Hardware Validation Engineer I", "robotics_test_validation"),
        ("Hardware Test Engineer I", "hardware_test"),
        ("Electromechanical Test Engineer", "hardware_test"),
        ("Embedded Hardware Validation Engineer", "electronics_test"),
        ("Manufacturing Test Engineer, Robotics", "manufacturing_test"),
        ("Manufacturing Validation Engineer", "manufacturing_test"),
        ("Manufacturing Test Engineer, Optimus", "manufacturing_test"),
        ("System Validation Engineer, Optimus Hand", "optimus_validation"),
        ("System Validation Engineer", None),
        ("Software Engineer, Optimus", None),
        ("Automation Engineer", "robotics_systems_integration"),
        ("Manufacturing Process Engineer", None),
        ("Machine Learning Systems Engineer", None),
        ("Robotics ML Systems Engineer", "robotics_ml_systems"),
        ("ROS 2 Engineer", "robotics_software"),
        ("Embedded Software Engineer", "embedded_systems"),
        ("Mechanical Engineer, Actuators", "mechanical_actuators"),
        ("Mechanical Design Engineer", None),
        ("Senior Counsel", None),
    ],
)
def test_role_family_requires_an_approved_title_signal(title, expected_family):
    family, _ = classify_role_family(title)
    assert family == expected_family


def test_narrow_electronics_signal_precedes_generic_embedded():
    family, signals = classify_role_family("Embedded Hardware Validation Engineer")
    assert family == "electronics_test"
    assert signals == ("embedded hardware validation",)


@pytest.mark.parametrize(
    "level",
    ["Senior", "Sr.", "Staff", "Principal", "Lead", "Manager", "Director", "Head", "Chief", "Fellow", "Executive"],
)
def test_senior_title_is_rejected(level, make_job):
    decision = stage_one(make_job(title=f"{level} Robotics Test Engineer"))
    assert decision.status is StageOneStatus.REJECT
    assert any("seniority" in reason.lower() for reason in decision.reasons)


@pytest.mark.parametrize(
    "title",
    [
        "Robotics Test Engineer I",
        "Robotics Test Engineer II",
        "Associate Robotics Test Engineer",
        "Entry Level Robotics Test Engineer",
        "Early Career Robotics Test Engineer",
        "Robotics Test Engineer",
    ],
)
def test_early_career_or_unlevelled_target_title_reaches_enrichment(title, make_job):
    decision = stage_one(make_job(title=title))
    assert decision.status is StageOneStatus.ENRICH


def test_lausanne_does_not_match_usa_substring(make_job):
    decision = stage_one(make_job(location="Lausanne, Switzerland", country_code=""))
    assert decision.status is StageOneStatus.REJECT
    assert "United States" in decision.reasons[0]


def test_conflicting_us_code_and_foreign_location_is_rejected(make_job):
    decision = stage_one(make_job(country_code="US", location="Toronto, Canada"))
    assert decision.status is StageOneStatus.REJECT
    assert "conflicting" in decision.reasons[0].lower()


@pytest.mark.parametrize("location", ["Remote", "Worldwide Remote", "Global"])
def test_unscoped_remote_is_withheld(make_job, location):
    decision = stage_one(make_job(location=location, country_code=""))
    assert decision.status is StageOneStatus.REJECT


def test_explicit_us_remote_is_accepted(make_job):
    job = make_job(
        location="Remote, United States",
        country_code="",
        employment_type=EmploymentType.FULL_TIME,
        provenance={"employment_type": FactSource.STRUCTURED_FEED},
    )
    assert stage_one(job).status is StageOneStatus.ENRICH


def test_city_and_state_location_is_us_without_country_code(make_job):
    assert is_us_location(make_job(location="Austin, TX", country_code="")) is True


def test_unknown_employment_can_enrich_but_is_not_confirmed(make_job):
    decision = stage_one(
        make_job(employment_type=EmploymentType.UNKNOWN, provenance={})
    )
    assert decision.status is StageOneStatus.ENRICH
    assert decision.employment_type is EmploymentType.UNKNOWN
    assert decision.full_time_confirmed is False


def test_official_detail_can_confirm_full_time(make_job):
    decision = stage_one(
        make_job(
            employment_type=EmploymentType.UNKNOWN,
            description="This is a full-time position on the robotics team.",
            provenance={"description": FactSource.OFFICIAL_DETAIL},
        )
    )
    assert decision.employment_type is EmploymentType.FULL_TIME
    assert decision.employment_source is FactSource.OFFICIAL_DETAIL
    assert decision.full_time_confirmed is True


def test_untagged_description_cannot_confirm_full_time(make_job):
    decision = stage_one(
        make_job(
            employment_type=EmploymentType.UNKNOWN,
            description="This is a full-time position on the robotics team.",
            provenance={},
        )
    )
    assert decision.employment_type is EmploymentType.UNKNOWN
    assert decision.full_time_confirmed is False


@pytest.mark.parametrize(
    "employment_type",
    [
        EmploymentType.PART_TIME,
        EmploymentType.CONTRACT,
        EmploymentType.TEMPORARY,
        EmploymentType.INTERNSHIP,
        EmploymentType.OTHER,
    ],
)
def test_non_full_time_employment_is_rejected(employment_type, make_job):
    decision = stage_one(
        make_job(
            employment_type=employment_type,
            provenance={"employment_type": FactSource.STRUCTURED_FEED},
        )
    )
    assert decision.status is StageOneStatus.REJECT


def test_factory_automation_context_refines_only_from_official_evidence(make_job):
    official = stage_one(
        make_job(
            title="Automation Engineer",
            description="Build factory production-line automation.",
            provenance={
                "description": FactSource.OFFICIAL_DETAIL,
                "employment_type": FactSource.STRUCTURED_FEED,
            },
        )
    )
    untagged = stage_one(
        make_job(
            title="Automation Engineer",
            description="Build factory production-line automation.",
            provenance={"employment_type": FactSource.STRUCTURED_FEED},
        )
    )
    assert official.role_family == "manufacturing_test"
    assert untagged.role_family == "robotics_systems_integration"


@pytest.mark.parametrize(
    ("company", "title", "expected_family"),
    [
        ("Figure", "Mechanical Test Engineer", "humanoid_mechanical_test"),
        ("Apptronik", "Mechanical Test Engineer", "humanoid_mechanical_test"),
        ("Zipline", "Mechanical Test Engineer", "flight_mechanical_test"),
        ("Skydio", "Mechanical Test Engineer", "flight_mechanical_test"),
    ],
)
def test_company_context_refines_mechanical_test_route(
    company, title, expected_family, make_job
):
    decision = stage_one(make_job(company=company, title=title))
    assert decision.role_family == expected_family
    assert decision.role_evidence[0] == "mechanical test engineer"
