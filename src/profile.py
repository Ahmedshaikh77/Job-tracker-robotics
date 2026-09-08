"""Strict, immutable loader for the candidate matching profile."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
import re
from types import MappingProxyType
from typing import Any

import yaml


class ProfileValidationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class CandidateEvidence:
    degree: str
    current_authorization: str
    stem_opt_eligible: bool
    future_sponsorship_needed: bool


@dataclass(frozen=True, slots=True)
class ScoringCaps:
    role_alignment: int
    technical_evidence: int
    experience_fit: int
    domain_alignment: int
    qualification_coverage: int
    authorization: int
    compensation: int
    recency: int


@dataclass(frozen=True, slots=True)
class ScoringPolicy:
    version: int
    preferred_experience_gap_penalty: int
    caps: ScoringCaps


@dataclass(frozen=True, slots=True)
class ResumeRoute:
    family: str
    filename: str


@dataclass(frozen=True, slots=True)
class MatchProfile:
    profile_version: int
    candidate: CandidateEvidence
    skills: Mapping[str, tuple[str, ...]]
    domains: tuple[str, ...]
    skill_aliases: Mapping[str, tuple[str, ...]]
    scoring: ScoringPolicy
    resume_routes: tuple[ResumeRoute, ...]


_TOP_KEYS = {
    "profile_version", "candidate", "skills", "domains", "skill_aliases",
    "scoring", "resume_routes",
}
_CANDIDATE_KEYS = {
    "degree", "current_authorization", "stem_opt_eligible",
    "future_sponsorship_needed",
}
_CAP_VALUES = {
    "role_alignment": 25,
    "technical_evidence": 15,
    "experience_fit": 15,
    "domain_alignment": 10,
    "qualification_coverage": 10,
    "authorization": 10,
    "compensation": 10,
    "recency": 5,
}
_ROUTES = (
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
    ("robotics_systems_integration", "Muhammad Ahmed Robotics Sensing Integration Engineer Tesla.pdf"),
    ("robotics_hardware_mechanical", "Muhammad Ahmed Mechanical Engineer Robotics Hardware.pdf"),
)


def _mapping(value: Any, name: str, keys: set[str] | None = None) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ProfileValidationError(f"{name} must be a mapping")
    if keys is not None and set(value) != keys:
        raise ProfileValidationError(f"invalid {name} keys")
    return value


def _string(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ProfileValidationError(f"{name} must be a non-empty string")
    return value


def _bool(value: Any, name: str) -> bool:
    if type(value) is not bool:
        raise ProfileValidationError(f"{name} must be a boolean")
    return value


def _sequence(value: Any, name: str) -> Sequence[Any]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ProfileValidationError(f"{name} must be a sequence")
    return value


def _normalise(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().casefold()


def load_profile(path: str | Path) -> MatchProfile:
    """Load only the approved evidence inventory and scoring configuration."""
    try:
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ProfileValidationError(f"cannot load profile: {exc}") from exc
    root = _mapping(raw, "profile", _TOP_KEYS)
    if type(root["profile_version"]) is not int or root["profile_version"] != 1:
        raise ProfileValidationError("unsupported profile version")

    candidate_raw = _mapping(root["candidate"], "candidate", _CANDIDATE_KEYS)
    candidate = CandidateEvidence(
        degree=_string(candidate_raw["degree"], "candidate.degree"),
        current_authorization=_string(
            candidate_raw["current_authorization"], "candidate.current_authorization"
        ),
        stem_opt_eligible=_bool(
            candidate_raw["stem_opt_eligible"], "candidate.stem_opt_eligible"
        ),
        future_sponsorship_needed=_bool(
            candidate_raw["future_sponsorship_needed"],
            "candidate.future_sponsorship_needed",
        ),
    )

    skills_raw = _mapping(root["skills"], "skills")
    if not skills_raw:
        raise ProfileValidationError("skills cannot be empty")
    skills: dict[str, tuple[str, ...]] = {}
    canonical: list[str] = []
    for category, values in skills_raw.items():
        category_name = _string(category, "skill category")
        entries = tuple(
            _string(value, f"skills.{category_name}")
            for value in _sequence(values, f"skills.{category_name}")
        )
        if not entries or len({_normalise(value) for value in entries}) != len(entries):
            raise ProfileValidationError("skills contain empty or duplicate values")
        skills[category_name] = entries
        canonical.extend(entries)
    if len({_normalise(value) for value in canonical}) != len(canonical):
        raise ProfileValidationError("canonical skills must be unique")

    domains = tuple(
        _string(value, "domain") for value in _sequence(root["domains"], "domains")
    )
    if not domains or len({_normalise(value) for value in domains}) != len(domains):
        raise ProfileValidationError("domains contain empty or duplicate values")

    aliases_raw = _mapping(root["skill_aliases"], "skill_aliases")
    if set(aliases_raw) != set(canonical):
        raise ProfileValidationError("every canonical skill needs exactly one alias entry")
    aliases: dict[str, tuple[str, ...]] = {}
    seen_aliases: set[str] = set()
    for skill in canonical:
        values = tuple(
            _string(value, f"skill_aliases.{skill}")
            for value in _sequence(aliases_raw[skill], f"skill_aliases.{skill}")
        )
        normalized = tuple(_normalise(value) for value in values)
        if not values or len(set(normalized)) != len(normalized):
            raise ProfileValidationError("aliases contain empty or duplicate values")
        if seen_aliases.intersection(normalized):
            raise ProfileValidationError("normalized aliases must be globally unique")
        seen_aliases.update(normalized)
        aliases[skill] = values

    scoring_raw = _mapping(
        root["scoring"],
        "scoring",
        {"version", "preferred_experience_gap_penalty", "caps"},
    )
    if type(scoring_raw["version"]) is not int or scoring_raw["version"] != 1:
        raise ProfileValidationError("unsupported scoring version")
    penalty = scoring_raw["preferred_experience_gap_penalty"]
    if type(penalty) is not int or penalty < 0:
        raise ProfileValidationError("preferred experience penalty must be non-negative")
    caps_raw = _mapping(scoring_raw["caps"], "scoring caps", set(_CAP_VALUES))
    if dict(caps_raw) != _CAP_VALUES or sum(caps_raw.values()) != 100:
        raise ProfileValidationError("scoring caps must match the version-one 100-point scale")
    caps = ScoringCaps(**dict(caps_raw))

    route_values = _sequence(root["resume_routes"], "resume_routes")
    route_pairs: list[tuple[str, str]] = []
    for value in route_values:
        route = _mapping(value, "resume route", {"family", "filename"})
        route_pairs.append(
            (
                _string(route["family"], "resume route family"),
                _string(route["filename"], "resume route filename"),
            )
        )
    if tuple(route_pairs) != _ROUTES:
        raise ProfileValidationError("resume routes must match the approved ordered inventory")
    if len({family for family, _ in route_pairs}) != len(route_pairs):
        raise ProfileValidationError("resume route families must be unique")

    return MatchProfile(
        profile_version=1,
        candidate=candidate,
        skills=MappingProxyType(skills),
        domains=domains,
        skill_aliases=MappingProxyType(aliases),
        scoring=ScoringPolicy(1, penalty, caps),
        resume_routes=tuple(ResumeRoute(*pair) for pair in route_pairs),
    )
