"""Classify work-authorization language without treating boilerplate as fact."""

from __future__ import annotations

from collections.abc import Mapping
import re

from .models import AuthorizationAssessment, AuthorizationStatus, FactSource


def classify_authorization(
    text: str,
    *,
    provenance: FactSource,
    candidate_authorization: Mapping[str, object] | None = None,
) -> AuthorizationAssessment:
    """Return a decisive assessment only for official, personally applicable text."""
    if provenance is not FactSource.OFFICIAL_DETAIL:
        return AuthorizationAssessment(
            AuthorizationStatus.UNKNOWN,
            "No official authorization evidence",
            FactSource.UNAVAILABLE,
        )

    normalized = re.sub(r"\s+", " ", text).strip()
    lower = normalized.casefold()
    citizenship_negated = bool(
        re.search(r"\b(?:u\.s\.?|us|united states) citizenship is not required\b", lower)
        or re.search(r"\bno (?:u\.s\.?|us|united states) citizenship required\b", lower)
    )

    block_patterns = [
        r"\b(?:cannot|can't|do not|don't|unable to|will not) sponsor(?:ship)?(?: now or in the future)?\b",
        r"\bno (?:visa|immigration) sponsorship (?:is |will be )?(?:available|offered|provided)\b",
        r"\b(?:we|the company|the employer) (?:do|does) not (?:offer|provide) (?:visa |immigration )?sponsorship\b",
        r"\b(?:visa |immigration )?sponsorship (?:is not|will not be) (?:available|eligible|offered|provided)(?: now or in the future)?\b",
        r"\bemployment[ -]based immigration sponsorship (?:is not|will not be) (?:available|eligible|offered|provided)\b",
        r"\bmust not (?:require|need) (?:visa |immigration )?sponsorship(?: now or in the future)?\b",
        r"\bwithout (?:visa |immigration )?sponsorship(?: now or in the future)?\b",
        r"\bwithout (?:the )?need for (?:visa |immigration )?sponsorship\b",
        r"\bnot eligible for (?:employment[ -]based )?(?:visa|immigration) sponsorship\b",
        r"\b(?:we|the company|the employer) (?:are |is )?(?:not able|unable) to (?:provide|offer) (?:visa |immigration )?sponsorship\b",
        r"\b(?:active|current) [a-z -]*clearance (?:is )?required\b",
        r"\b(?:security )?clearance (?:is )?required\b",
        r"\bmust (?:obtain|hold|maintain) (?:an? )?[a-z -]*clearance\b",
        r"\bmust be eligible to (?:obtain|hold)(?: and (?:obtain|hold|maintain))? (?:an? )?[a-z -]*clearance\b",
        r"\bmust be eligible for (?:an? )?[a-z -]*clearance\b",
        r"\b(?:limited to|applicants? must be|must be) (?:a |an )?(?:u\.s\.?|us|united states) persons?\b",
        r"\bmust (?:qualify|be eligible) as (?:a |an )?(?:u\.s\.?|us|united states) persons?\b",
        r"\b(?:itar|export control)[^.]{0,100}\b(?:u\.s\.?|us|united states) persons?\b",
        r"\b(?:lawful permanent residency|permanent resident status|permanent residency|green card) (?:is )?required\b",
        r"\bmust (?:hold|possess|have) (?:a )?(?:valid )?green card\b",
        r"\b(?:applicants?|candidates?) must be (?:lawful )?(?:u\.s\.? )?permanent residents?\b",
        r"\b(?:applicants?|candidates?) must be (?:valid )?green card holders?\b",
        r"(?:^|[.;:]\s*)green card holders?(?:[.;]|$)",
    ]
    if not citizenship_negated:
        block_patterns.append(
            r"\b(?:must be|requires?|required)[^.]{0,50}(?:u\.s\.?|us|united states) citizens?\b|"
            r"\b(?:u\.s\.?|us|united states) citizenship (?:is )?required\b|"
            r"\bmust be (?:an? )?citizens? of (?:the )?(?:u\.s\.?|us|united states)\b|"
            r"(?:^|[.;:]\s*)(?:u\.s\.?|us|united states) citizens?(?:[.;]|$)"
        )
    for pattern in block_patterns:
        if match := re.search(pattern, lower):
            return AuthorizationAssessment(
                AuthorizationStatus.BLOCKED,
                match.group(0),
                FactSource.OFFICIAL_DETAIL,
            )

    support = re.search(
        r"\b(?:visa|immigration) sponsorship (?:is |will be )?(?:available|offered|provided)\b|"
        r"\bwe (?:offer|provide) (?:visa |immigration )?sponsorship\b",
        lower,
    )
    if support:
        clause_start = max(lower.rfind(mark, 0, support.start()) for mark in ".;!?")
        clause_ends = [
            index
            for mark in ".;!?"
            if (index := lower.find(mark, support.end())) >= 0
        ]
        clause_end = min(clause_ends) if clause_ends else len(lower)
        clause = lower[clause_start + 1 : clause_end]
        if not re.search(
            r"\b(?:no|not|never|without|cannot|can't|unable)\b|\b(?:do|does|will) not\b",
            clause,
        ):
            return AuthorizationAssessment(
                AuthorizationStatus.CONFIRMED_SUPPORT,
                support.group(0),
                FactSource.OFFICIAL_DETAIL,
            )

    at_hire = re.search(
        r"\b(?:legally )?authorized(?: to work (?:in )?(?:the )?united states)? at (?:the )?(?:time of )?hire\b",
        lower,
    )
    if at_hire and candidate_authorization:
        current = str(candidate_authorization.get("current_authorization", "")).casefold()
        stem = candidate_authorization.get("stem_opt_eligible") is True
        future = candidate_authorization.get("future_sponsorship_needed") is True
        if "opt" in current and stem and future:
            return AuthorizationAssessment(
                AuthorizationStatus.OPT_COMPATIBLE_UNCERTAIN,
                "At-hire authorization is compatible with the candidate's current F-1 OPT; future sponsorship remains uncertain",
                FactSource.TRACKER_INFERENCE,
            )

    return AuthorizationAssessment(
        AuthorizationStatus.UNKNOWN,
        "No decisive personal authorization restriction published",
        FactSource.OFFICIAL_DETAIL,
    )
