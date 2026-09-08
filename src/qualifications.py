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
_REVIEW_ALTERNATIVE_RE = re.compile(
    r"\bor\s+(?:an?\s+)?((?:equivalent|related)\s+(?:(?:work|practical)\s+)?experience"
    r"|related (?:technical )?field)\b"
)
_NEGATED_ALTERNATIVE_RE = re.compile(
    r"^\s*(?:(?:that|which)\s+)?(?:(?:is|are|will|would|can|may|shall)\s+)?"
    r"(?:not|never|cannot|can't|won't|isn't|aren't)\b"
)


def _normalise(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().casefold()


def _contains(text: str, phrase: str) -> bool:
    return bool(
        re.search(
            rf"(?<![a-z0-9]){re.escape(_normalise(phrase))}(?![a-z0-9])",
            _normalise(text),
        )
    )


def _degree_group(clause: str, profile: MatchProfile) -> str | None:
    degree_word = re.search(r"\bdegree\b", clause)
    if not degree_word:
        return None
    # Section parsing flattens line breaks. A named skill after the degree
    # starts a separate requirement; its alternatives cannot relax the degree.
    for aliases in profile.skill_aliases.values():
        for alias in aliases:
            skill = re.search(
                rf"(?<![a-z0-9]){re.escape(_normalise(alias))}(?![a-z0-9])",
                clause[degree_word.end():],
            )
            if skill:
                clause = clause[:degree_word.end() + skill.start()]
    fields = [field for field in _DEGREE_FIELDS if _contains(clause, field)]
    # Broad categories are accepted only when the posting explicitly names them.
    # A specific electrical/computer engineering field never becomes "engineering".
    if re.search(r"\btechnical degree\b", clause):
        fields.append("technical degree")
    if re.search(
        r"(?:\bdegree in\s+|,\s*|\bor\s+)engineering(?=$|[.,]|\s+(?:or|and|is|required)\b)"
        r"|(?:^|\ban?\s+)engineering degree\b",
        clause,
    ):
        fields.append("engineering")
    alternatives = [
        match.group(1) for match in _REVIEW_ALTERNATIVE_RE.finditer(clause)
        if not _NEGATED_ALTERNATIVE_RE.match(clause[match.end():])
    ]
    if not fields and not alternatives:
        return None
    kind = "degree-review" if alternatives else "degree"
    return f"{kind}:{'|'.join(dict.fromkeys(fields + alternatives))}"


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
        # Semicolons and flattened bullet lists separate independent requirements.
        for clause in re.split(r";|\s+-\s+|[•\n]", lower):
            degree = _degree_group(clause, profile)
            if degree:
                groups.append(degree)
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
    candidate_degree_fields = {
        field
        for field in _DEGREE_FIELDS
        if _contains(profile.candidate.degree, field)
    }
    matched: list[str] = []
    for group in dict.fromkeys(required_groups):
        kind, separator, value = group.partition(":")
        if not separator:
            continue
        if kind == "skill" and value in canonical_skills:
            matched.append(group)
        elif kind == "domain" and value in domains:
            matched.append(group)
        elif kind in {"degree", "degree-review"}:
            alternatives = {_normalise(item) for item in value.split("|")}
            # This limited broad-field rule requires an explicitly evidenced,
            # recognized engineering discipline; it never infers experience.
            broad_engineering_match = (
                bool(alternatives.intersection({"engineering", "technical degree"}))
                and any(field.endswith(" engineering") for field in candidate_degree_fields)
            )
            if alternatives.intersection(candidate_degree_fields) or broad_engineering_match:
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
    missing = tuple(
        group for group in required
        if group not in matched_set and not group.startswith("degree-review:")
    )
    # Unconfirmed alternatives remain in the denominator and earn zero credit,
    # but are not asserted to be definite missing qualifications.
    points = round(10 * len(matched) / len(required)) if required else 5
    gaps: tuple[str, ...] = ()
    if preferred_experience_above_three:
        points = max(0, points - penalty_points)
        gaps = ("preferred experience above three years",)
    return QualificationAssessment(required, matched, missing, gaps, points)


def qualification_gap(assessment: QualificationAssessment) -> str:
    """Describe definite gaps or an unconfirmed degree alternative for a reader."""
    if assessment.missing_required_groups:
        group = assessment.missing_required_groups[0]
        kind, _, value = group.partition(":")
        if kind == "degree":
            label = f"degree in {' or '.join(value.split('|'))}"
        elif kind in {"skill", "domain"}:
            label = value
        else:
            label = group
        return f"Missing required qualification: {label}"
    for group in assessment.required_groups:
        if group.startswith("degree-review:") and group not in assessment.matched_required_groups:
            alternatives = group.partition(":")[2].split("|")
            review_paths = [
                item for item in alternatives
                if "experience" in item or item in {"related field", "related technical field"}
            ]
            return (
                "Review degree requirement: posting allows "
                f"{' or '.join(review_paths)}; candidate fit is not confirmed"
            )
    return ""
