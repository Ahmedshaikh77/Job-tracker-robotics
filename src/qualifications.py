"""Extract and match explicit required qualification groups."""

from __future__ import annotations

import re

from .models import FactSource, QualificationAssessment
from .parsing import JobSections
from .profile import MatchProfile


_DEGREE_FIELDS = (
    "mechanical engineering",
    "materials science",
    "electrical engineering",
    "computer science",
    "civil engineering",
    "computer engineering",
    "aerospace engineering",
)
_CANDIDATE_DEGREE_FIELDS = frozenset({"mechanical engineering", "materials science"})


def _normalise(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().casefold()


def _contains(text: str, phrase: str) -> bool:
    return bool(
        re.search(
            rf"(?<![a-z0-9]){re.escape(_normalise(phrase))}(?![a-z0-9])",
            _normalise(text),
        )
    )


def extract_qualification_groups(
    sections: JobSections,
    profile: MatchProfile,
    *,
    provenance: FactSource,
) -> tuple[str, ...]:
    """Extract canonical evidence only from official required sections."""
    if provenance is not FactSource.OFFICIAL_DETAIL:
        return ()
    groups: list[str] = []
    for sentence in sections.required:
        lower = _normalise(sentence)
        if "degree" in lower:
            fields = tuple(field for field in _DEGREE_FIELDS if _contains(lower, field))
            if fields:
                groups.append(f"degree:{'|'.join(fields)}")
        for skill, aliases in profile.skill_aliases.items():
            if any(_contains(lower, alias) for alias in aliases):
                groups.append(f"skill:{skill}")
        for domain in profile.domains:
            if _contains(lower, domain):
                groups.append(f"domain:{domain}")
    return tuple(dict.fromkeys(groups))


def match_qualification_groups(
    required_groups: tuple[str, ...],
    profile: MatchProfile,
) -> tuple[str, ...]:
    """Match requirements against only the candidate evidence in the profile."""
    canonical_skills = {
        skill for values in profile.skills.values() for skill in values
    }
    domains = set(profile.domains)
    matched: list[str] = []
    for group in dict.fromkeys(required_groups):
        kind, separator, value = group.partition(":")
        if not separator:
            continue
        if kind == "skill" and value in canonical_skills:
            matched.append(group)
        elif kind == "domain" and value in domains:
            matched.append(group)
        elif kind == "degree":
            alternatives = {_normalise(item) for item in value.split("|")}
            if alternatives.intersection(_CANDIDATE_DEGREE_FIELDS):
                matched.append(group)
    return tuple(matched)


def assess_qualifications(
    required_groups: tuple[str, ...],
    matched_groups: tuple[str, ...],
    *,
    preferred_experience_above_three: bool,
    penalty_points: int = 2,
) -> QualificationAssessment:
    """Calculate literal required-group coverage and the preferred-years gap."""
    required = tuple(dict.fromkeys(required_groups))
    matched_set = set(matched_groups)
    matched = tuple(group for group in required if group in matched_set)
    missing = tuple(group for group in required if group not in matched_set)
    points = round(10 * len(matched) / len(required)) if required else 5
    gaps: tuple[str, ...] = ()
    if preferred_experience_above_three:
        points = max(0, points - penalty_points)
        gaps = ("preferred experience above three years",)
    return QualificationAssessment(required, matched, missing, gaps, points)
