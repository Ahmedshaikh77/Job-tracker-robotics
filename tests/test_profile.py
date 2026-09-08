from __future__ import annotations

from copy import deepcopy
from dataclasses import fields
from pathlib import Path

import pytest
import yaml

from src.profile import MatchProfile, ProfileValidationError, load_profile


PROFILE_PATH = Path(__file__).parents[1] / "profile.yaml"


EXPECTED_ROUTES = (
    ("camera_optical", "Muhammad Ahmed Mechanical Engineer Camera Optical Zipline.pdf"),
    ("mechanical_actuators", "Muhammad Ahmed Mechanical Engineer Actuators.pdf"),
    ("medical_robotics_software", "Muhammad Ahmed Software Engineer Medical Robotics.pdf"),
    ("robotics_ml_systems", "Muhammad Ahmed Robotics ML Systems Engineer.pdf"),
    ("junior_embedded", "Muhammad Ahmed Junior Embedded Engineer.pdf"),
    ("embedded_systems", "Embedded_Software_Engineer.pdf"),
    ("electronics_test", "Muhammad Ahmed Electronics Test Engineer.pdf"),
    ("optimus_validation", "Mechatronics Engineer, Optimus Hardware Validation .pdf"),
    ("robotics_test_validation", "Mechatronics Engineer, Optimus Hardware Validation .pdf"),
    ("humanoid_mechanical_test", "Muhammad Ahmed Mechanical Test Engineer Figure.pdf"),
    ("flight_mechanical_test", "Muhammad Ahmed Mechanical Test Engineer Zipline.pdf"),
    ("manufacturing_test", "Muhammad Ahmed Manufacturing Test Engineer Tesla Optimus.pdf"),
    ("hardware_test", "Muhammad Ahmed Hardware Test Engineer.pdf"),
    ("robotics_software", "Muhammad Ahmed Robotics Software Engineer Fluidstack.pdf"),
    (
        "robotics_systems_integration",
        "Muhammad Ahmed Robotics Sensing Integration Engineer Tesla.pdf",
    ),
    ("robotics_hardware_mechanical", "Muhammad Ahmed Mechanical Engineer Robotics Hardware.pdf"),
)


def valid_profile_dict() -> dict:
    return {
        "profile_version": 1,
        "candidate": {
            "degree": "MS Mechanical Engineering and Materials Science, Duke University",
            "current_authorization": "F-1 OPT",
            "stem_opt_eligible": True,
            "future_sponsorship_needed": True,
        },
        "skills": {
            "programming": ["Python", "C", "C++", "MATLAB"],
            "robotics": ["ROS 2", "MoveIt 2"],
            "platforms": ["Linux", "Git", "ESP32", "Jetson"],
            "cad": ["SolidWorks", "Fusion 360"],
            "sensing": ["RealSense", "IMU", "load cell", "encoder", "camera"],
            "actuation": ["motor", "actuator"],
        },
        "domains": [
            "robotics systems",
            "electromechanical systems",
            "embedded systems",
            "mechanical design",
            "sensor integration",
            "test automation",
            "hardware validation",
            "system integration",
            "failure analysis",
        ],
        "skill_aliases": {
            "Python": ["python"],
            "C": ["c programming", "embedded c"],
            "C++": ["c++", "cpp"],
            "MATLAB": ["matlab"],
            "ROS 2": ["ros 2", "ros2"],
            "MoveIt 2": ["moveit 2", "moveit2"],
            "Linux": ["linux"],
            "Git": ["git"],
            "SolidWorks": ["solidworks"],
            "Fusion 360": ["fusion 360"],
            "ESP32": ["esp32"],
            "Jetson": ["jetson"],
            "RealSense": ["realsense"],
            "IMU": ["imu", "inertial measurement unit"],
            "load cell": ["load cell"],
            "encoder": ["encoder"],
            "camera": ["camera", "imaging sensor"],
            "motor": ["motor"],
            "actuator": ["actuator"],
        },
        "scoring": {
            "version": 1,
            "preferred_experience_gap_penalty": 2,
            "caps": {
                "role_alignment": 25,
                "technical_evidence": 15,
                "experience_fit": 15,
                "domain_alignment": 10,
                "qualification_coverage": 10,
                "authorization": 10,
                "compensation": 10,
                "recency": 5,
            },
        },
        "resume_routes": [
            {"family": family, "filename": filename}
            for family, filename in EXPECTED_ROUTES
        ],
    }


def _write_profile(tmp_path, payload: dict) -> Path:
    path = tmp_path / "profile.yaml"
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    return path


def test_checked_in_profile_loads_as_immutable_typed_profile():
    profile = load_profile(PROFILE_PATH)
    assert isinstance(profile, MatchProfile)
    assert profile.candidate.stem_opt_eligible is True
    assert profile.candidate.future_sponsorship_needed is True
    assert sum(
        getattr(profile.scoring.caps, item.name)
        for item in fields(profile.scoring.caps)
    ) == 100
    assert tuple((route.family, route.filename) for route in profile.resume_routes) == EXPECTED_ROUTES
    with pytest.raises(TypeError):
        profile.skills["programming"] = ("Rust",)


def test_sensing_route_uses_actual_tesla_resume_filename():
    profile = load_profile(PROFILE_PATH)
    route = next(
        route
        for route in profile.resume_routes
        if route.family == "robotics_systems_integration"
    )
    assert route.filename == "Muhammad Ahmed Robotics Sensing Integration Engineer Tesla.pdf"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda data: data.update({"unexpected": True}),
        lambda data: data["candidate"].update({"stem_opt_eligible": "true"}),
        lambda data: data.update({"profile_version": 2}),
        lambda data: data["scoring"].update({"version": 2}),
        lambda data: data["skills"]["programming"].append("Rust"),
        lambda data: data["skill_aliases"].update({"Ghost": ["ghost"]}),
        lambda data: data["skill_aliases"]["Python"].append("PYTHON"),
        lambda data: data["resume_routes"].pop(),
        lambda data: data["resume_routes"].append(
            {"family": "generic", "filename": "Generic.pdf"}
        ),
        lambda data: data["resume_routes"].append(deepcopy(data["resume_routes"][0])),
        lambda data: data["resume_routes"].__setitem__(
            0, {"family": "camera_optical", "filename": "Wrong.pdf"}
        ),
        lambda data: data["resume_routes"].reverse(),
    ],
)
def test_profile_validation_rejects_malformed_or_unapproved_configuration(
    tmp_path, mutate
):
    payload = valid_profile_dict()
    mutate(payload)
    with pytest.raises(ProfileValidationError):
        load_profile(_write_profile(tmp_path, payload))


def test_profile_validation_rejects_wrong_scoring_cap(tmp_path):
    payload = valid_profile_dict()
    payload["scoring"]["caps"]["role_alignment"] = 24
    with pytest.raises(ProfileValidationError, match="caps"):
        load_profile(_write_profile(tmp_path, payload))


def test_profile_validation_rejects_numerically_equal_float_cap(tmp_path):
    payload = valid_profile_dict()
    payload["scoring"]["caps"]["role_alignment"] = 25.0
    with pytest.raises(ProfileValidationError, match="caps"):
        load_profile(_write_profile(tmp_path, payload))
