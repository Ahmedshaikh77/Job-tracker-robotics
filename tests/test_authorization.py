from __future__ import annotations

import pytest

from src.authorization import classify_authorization
from src.models import AuthorizationStatus, FactSource


@pytest.mark.parametrize(
    "text",
    [
        "Must be a U.S. citizen.",
        "Active Secret clearance required.",
        "This position is limited to U.S. persons under ITAR.",
        "We cannot sponsor now or in the future.",
    ],
)
def test_direct_personal_restrictions_are_blocked(text):
    result = classify_authorization(text, provenance=FactSource.OFFICIAL_DETAIL)
    assert result.status is AuthorizationStatus.BLOCKED
    assert result.source is FactSource.OFFICIAL_DETAIL


def test_general_export_boilerplate_is_not_a_personal_block():
    result = classify_authorization(
        "Products may be subject to U.S. export control laws.",
        provenance=FactSource.OFFICIAL_DETAIL,
    )
    assert result.status is AuthorizationStatus.UNKNOWN


def test_citizenship_not_required_negates_block():
    result = classify_authorization(
        "U.S. citizenship is not required for this position.",
        provenance=FactSource.OFFICIAL_DETAIL,
    )
    assert result.status is AuthorizationStatus.UNKNOWN


def test_direct_sponsorship_offer_is_confirmed_support():
    result = classify_authorization(
        "Visa sponsorship is available for qualified candidates.",
        provenance=FactSource.OFFICIAL_DETAIL,
    )
    assert result.status is AuthorizationStatus.CONFIRMED_SUPPORT
    assert result.source is FactSource.OFFICIAL_DETAIL


def test_authorized_at_hire_supports_opt_inference_not_confirmed_sponsorship():
    result = classify_authorization(
        "Must be legally authorized to work in the United States at time of hire.",
        provenance=FactSource.OFFICIAL_DETAIL,
        candidate_authorization={
            "current_authorization": "F-1 OPT",
            "stem_opt_eligible": True,
            "future_sponsorship_needed": True,
        },
    )
    assert result.status is AuthorizationStatus.OPT_COMPATIBLE_UNCERTAIN
    assert result.source is FactSource.TRACKER_INFERENCE


def test_authorized_at_hire_without_candidate_context_stays_unknown():
    result = classify_authorization(
        "Must be legally authorized to work in the United States at time of hire.",
        provenance=FactSource.OFFICIAL_DETAIL,
    )
    assert result.status is AuthorizationStatus.UNKNOWN


def test_short_authorized_at_hire_wording_supports_candidate_specific_inference():
    result = classify_authorization(
        "Must be authorized at hire.",
        provenance=FactSource.OFFICIAL_DETAIL,
        candidate_authorization={
            "current_authorization": "F-1 OPT",
            "stem_opt_eligible": True,
            "future_sponsorship_needed": True,
        },
    )
    assert result.status is AuthorizationStatus.OPT_COMPATIBLE_UNCERTAIN
    assert result.source is FactSource.TRACKER_INFERENCE


@pytest.mark.parametrize(
    "text",
    [
        "Visa sponsorship is not available now or in the future.",
        "Candidates must obtain a Secret clearance.",
        "Applicants must qualify as U.S. persons.",
    ],
)
def test_common_direct_restriction_wording_is_blocked(text):
    assert classify_authorization(
        text, provenance=FactSource.OFFICIAL_DETAIL
    ).status is AuthorizationStatus.BLOCKED


@pytest.mark.parametrize(
    "text",
    [
        "Visa sponsorship is available.",
        "We cannot sponsor now or in the future.",
        "Must be a U.S. citizen.",
        "Active clearance required.",
        "Applicants must be U.S. persons.",
        "Applicant must satisfy ITAR restrictions as a U.S. person.",
    ],
)
def test_structured_feed_cannot_confirm_or_block_authorization(text):
    result = classify_authorization(text, provenance=FactSource.STRUCTURED_FEED)
    assert result.status is AuthorizationStatus.UNKNOWN
    assert result.source is FactSource.UNAVAILABLE


def test_tracker_inference_text_cannot_create_authorization_fact():
    result = classify_authorization(
        "Visa sponsorship is available.",
        provenance=FactSource.TRACKER_INFERENCE,
    )
    assert result.status is AuthorizationStatus.UNKNOWN
    assert result.source is FactSource.UNAVAILABLE
