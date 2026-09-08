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

    support = re.search(
        r"\b(?:visa|immigration) sponsorship (?:is |will be )?(?:available|offered|provided)\b|"
        r"\bwe (?:offer|provide) (?:visa |immigration )?sponsorship\b",
        lower,
    )
    if support:
        return AuthorizationAssessment(
            AuthorizationStatus.CONFIRMED_SUPPORT,
            support.group(0),
            FactSource.OFFICIAL_DETAIL,
        )

    block_patterns = [
        r"\b(?:cannot|can't|do not|don't|unable to|will not) sponsor(?:ship)?(?: now or in the future)?\b",
        r"\b(?:active|current) [a-z -]*clearance (?:is )?required\b",
        r"\b(?:security )?clearance (?:is )?required\b",
        r"\b(?:limited to|applicants? must be|must be) (?:a |an )?(?:u\.s\.?|us|united states) persons?\b",
        r"\b(?:itar|export control)[^.]{0,100}\b(?:u\.s\.?|us|united states) persons?\b",
    ]
    if not citizenship_negated:
        block_patterns.append(
            r"\b(?:must be|requires?|required)[^.]{0,50}(?:u\.s\.?|us|united states) citizens?\b|"
            r"\b(?:u\.s\.?|us|united states) citizenship (?:is )?required\b"
        )
    for pattern in block_patterns:
        if match := re.search(pattern, lower):
            return AuthorizationAssessment(
                AuthorizationStatus.BLOCKED,
                match.group(0),
                FactSource.OFFICIAL_DETAIL,
            )

    at_hire = re.search(
        r"\blegally authorized to work (?:in )?(?:the )?united states at (?:the )?time of hire\b",
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
