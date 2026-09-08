from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from src.eligibility import stage_one
from src.profile import load_profile
from src.resumes import ResumeRouter


@pytest.fixture(scope="module")
def router():
    profile = load_profile(Path(__file__).parents[1] / "profile.yaml")
    return ResumeRouter(profile.resume_routes)


@pytest.mark.parametrize(
    ("company", "title", "family", "filename"),
    [
        ("Zipline", "Optical Engineer", "camera_optical", "Muhammad Ahmed Mechanical Engineer Camera Optical Zipline.pdf"),
        ("Figure", "Mechanical Engineer, Actuators", "mechanical_actuators", "Muhammad Ahmed Mechanical Engineer Actuators.pdf"),
        ("MedTech", "Medical Robotics Software Engineer", "medical_robotics_software", "Muhammad Ahmed Software Engineer Medical Robotics.pdf"),
        ("Robotics Co", "Robotics ML Systems Engineer", "robotics_ml_systems", "Muhammad Ahmed Robotics ML Systems Engineer.pdf"),
        ("Robotics Co", "Junior Embedded Engineer", "junior_embedded", "Muhammad Ahmed Junior Embedded Engineer.pdf"),
        ("Robotics Co", "Embedded Systems Engineer", "embedded_systems", "Embedded_Software_Engineer.pdf"),
        ("Robotics Co", "Electronics Test Engineer", "electronics_test", "Muhammad Ahmed Electronics Test Engineer.pdf"),
        ("Tesla", "System Validation Engineer, Optimus", "optimus_validation", "Mechatronics Engineer, Optimus Hardware Validation .pdf"),
        ("Robotics Co", "Robotics Test Engineer", "robotics_test_validation", "Mechatronics Engineer, Optimus Hardware Validation .pdf"),
        ("Figure", "Mechanical Test Engineer", "humanoid_mechanical_test", "Muhammad Ahmed Mechanical Test Engineer Figure.pdf"),
        ("Zipline", "Mechanical Test Engineer", "flight_mechanical_test", "Muhammad Ahmed Mechanical Test Engineer Zipline.pdf"),
        ("Tesla", "Manufacturing Test Engineer", "manufacturing_test", "Muhammad Ahmed Manufacturing Test Engineer Tesla Optimus.pdf"),
        ("Robotics Co", "Hardware Test Engineer", "hardware_test", "Muhammad Ahmed Hardware Test Engineer.pdf"),
        ("Robotics Co", "Robotics Software Engineer", "robotics_software", "Muhammad Ahmed Robotics Software Engineer Fluidstack.pdf"),
        ("Tesla", "Sensor Integration Engineer", "robotics_systems_integration", "Muhammad Ahmed Robotics Sensing Integration Engineer Tesla.pdf"),
        ("Robotics Co", "Robotics Hardware Engineer", "robotics_hardware_mechanical", "Muhammad Ahmed Mechanical Engineer Robotics Hardware.pdf"),
    ],
)
def test_every_approved_family_routes_to_exact_resume(
    company, title, family, filename, router, make_job
):
    decision = stage_one(make_job(company=company, title=title))
    result = router.select(decision)
    assert decision.role_family == family
    assert result.filename == filename
    assert result.role_family == family
    assert result.evidence == decision.role_evidence
    assert result.reason == "; ".join(decision.role_evidence)


def test_unmapped_role_has_no_generic_fallback(router, make_job):
    decision = stage_one(make_job(title="Facilities Planner"))
    assert router.select(decision) is None


@pytest.mark.parametrize(
    ("title", "expected_family"),
    [
        ("Embedded Hardware Validation Engineer", "electronics_test"),
        ("Hardware Validation Engineer", "robotics_test_validation"),
        ("Junior Embedded Systems Engineer", "junior_embedded"),
        ("Mechanical Engineer, Optical Actuator", "camera_optical"),
        ("Manufacturing Test Engineer, Optimus", "manufacturing_test"),
    ],
)
def test_narrow_routes_win_precedence(title, expected_family, router, make_job):
    decision = stage_one(make_job(title=title))
    result = router.select(decision)
    assert result.role_family == expected_family


def test_factory_context_routes_automation_to_manufacturing(router, make_job):
    from src.models import FactSource

    decision = stage_one(
        make_job(
            title="Automation Engineer",
            description="Own factory production-line test automation.",
            provenance={"description": FactSource.OFFICIAL_DETAIL},
        )
    )
    assert router.select(decision).role_family == "manufacturing_test"


def test_mapped_role_without_exact_evidence_is_rejected(router, make_job):
    decision = replace(
        stage_one(make_job(title="Robotics Test Engineer")), role_evidence=()
    )
    with pytest.raises(ValueError, match="evidence"):
        router.select(decision)
