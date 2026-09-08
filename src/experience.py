"""Parse required and preferred experience from official job text."""

from __future__ import annotations

import re

from .models import ExperienceRequirement, FactSource
from .parsing import JobSections


_RANGE_RE = re.compile(
    r"\b(\d+(?:\.\d+)?)\s*(?:-|\u2013|\u2014|to)\s*(\d+(?:\.\d+)?)\s*years?\b",
    re.IGNORECASE,
)
_PLUS_RE = re.compile(r"\b(\d+(?:\.\d+)?)\s*\+\s*years?\b", re.IGNORECASE)
_MINIMUM_RE = re.compile(
    r"\bminimum(?:\s+of)?\s+(\d+(?:\.\d+)?)\s*years?\b", re.IGNORECASE
)
_PLAIN_RE = re.compile(r"\b(\d+(?:\.\d+)?)\s*years?\b", re.IGNORECASE)
_EARLY_TITLE_RE = re.compile(
    r"\b(?:junior|associate|entry[ -]level|early[ -]career|new grad(?:uate)?)\b|"
    r"\bengineer\s+(?:i|ii|1|2)\b",
    re.IGNORECASE,
)
_TEAM_RE = re.compile(
    r"\b(?:within|on|with|part of)\s+(?:a\s+)?(?:multidisciplinary\s+|cross[ -]functional\s+)?team\b|"
    r"\bcollaborat(?:e|es|ing)\b",
    re.IGNORECASE,
)
_HANDS_ON_RE = re.compile(
    r"\b(?:build|test|validate|design|implement|develop|document|integrate|troubleshoot)\b",
    re.IGNORECASE,
)
_LEADERSHIP_RE = re.compile(
    r"\b(?:own|owns|ownership|architect(?:ure)?|technical direction|roadmap|hire|hiring|"
    r"mentor|mentoring|manage|manager|management|lead|leadership)\b",
    re.IGNORECASE,
)


def _number(value: str) -> float:
    parsed = float(value)
    return int(parsed) if parsed.is_integer() else parsed


def _bounds(sentence: str) -> tuple[float | None, float | None]:
    if match := _RANGE_RE.search(sentence):
        return _number(match.group(1)), _number(match.group(2))
    if match := _PLUS_RE.search(sentence):
        return _number(match.group(1)), None
    if match := _MINIMUM_RE.search(sentence):
        return _number(match.group(1)), None
    if match := _PLAIN_RE.search(sentence):
        return _number(match.group(1)), None
    return None, None


def _first_numeric(
    sentences: tuple[str, ...],
) -> tuple[float | None, float | None, str]:
    for sentence in sentences:
        minimum, maximum = _bounds(sentence)
        if minimum is not None:
            return minimum, maximum, sentence
    return None, None, ""


def _degree_substitution(sentence: str) -> tuple[float | None, str]:
    lower = sentence.casefold()
    bachelor = re.search(
        r"(\d+(?:\.\d+)?)\s*years?[^\d.;]{0,80}?bachelor(?:'s)?\s+degree", lower
    )
    master = re.search(
        r"(?:or\s+)?(\d+(?:\.\d+)?)\s*years?[^\d.;]{0,80}?master(?:'s)?\s+degree",
        lower,
    )
    if bachelor and master:
        effective = _number(master.group(1))
        return effective, f"Master's degree alternative reduces the effective minimum to {effective:g} years"
    return None, ""


def _responsibilities_support_early_career(sentences: tuple[str, ...]) -> tuple[bool, str]:
    joined = " ".join(sentences)
    if not joined or _LEADERSHIP_RE.search(joined):
        return False, ""
    if _TEAM_RE.search(joined) and _HANDS_ON_RE.search(joined):
        return True, next(
            sentence
            for sentence in sentences
            if _TEAM_RE.search(sentence) or _HANDS_ON_RE.search(sentence)
        )
    return False, ""


def parse_experience(
    sections: JobSections,
    *,
    title: str,
    provenance: FactSource,
) -> ExperienceRequirement:
    """Parse numeric bounds only when the description is official detail text."""
    official = provenance is FactSource.OFFICIAL_DETAIL
    stated_min: float | None = None
    stated_max: float | None = None
    preferred_min: float | None = None
    preferred_max: float | None = None
    required_evidence = ""
    preferred_evidence = ""
    if official:
        stated_min, stated_max, required_evidence = _first_numeric(sections.required)
        preferred_min, preferred_max, preferred_evidence = _first_numeric(
            sections.preferred
        )

    effective_min = stated_min
    effective_max = stated_max
    flexible = False
    flexibility_basis = ""
    if required_evidence and stated_min is not None:
        substitution, substitution_basis = _degree_substitution(required_evidence)
        if substitution is not None and substitution <= 3:
            effective_min = substitution
            flexible = True
            flexibility_basis = substitution_basis
        elif "typical" in required_evidence.casefold() and stated_min > 3:
            flexible = True
            flexibility_basis = "Typical experience wording"
        elif stated_max is not None and stated_min <= 3 < stated_max:
            flexible = True
            flexibility_basis = "Published range includes three years"

    title_support = bool(_EARLY_TITLE_RE.search(title))
    responsibility_support, responsibility_evidence = _responsibilities_support_early_career(
        sections.responsibilities if official else ()
    )
    unresolved = stated_min is None
    early_supported = unresolved and (title_support or responsibility_support)
    evidence = required_evidence or preferred_evidence
    if unresolved:
        if title_support:
            evidence = f"Early-career title: {title}"
        elif responsibility_support:
            evidence = responsibility_evidence
        else:
            evidence = "No required years published"

    return ExperienceRequirement(
        stated_required_minimum=stated_min,
        stated_required_maximum=stated_max,
        effective_required_minimum=effective_min,
        effective_required_maximum=effective_max,
        preferred_minimum=preferred_min,
        preferred_maximum=preferred_max,
        flexible=flexible,
        flexibility_basis=flexibility_basis,
        unresolved=unresolved,
        early_career_supported=early_supported,
        evidence=evidence,
        source=FactSource.OFFICIAL_DETAIL if official else FactSource.UNAVAILABLE,
    )
