from __future__ import annotations

from decimal import Decimal

import pytest

from src.compensation import (
    CompensationParseStatus,
    ParsedCompensation,
    assess_compensation,
    parse_compensation,
)
from src.models import (
    CompensationStatus,
    FactSource,
    PayPeriod,
    SalaryRange,
)
from src.parsing import split_job_sections


@pytest.mark.parametrize(
    ("minimum", "maximum", "period", "currency", "status", "label", "points"),
    [
        ("100000", "140000", PayPeriod.YEAR, "USD", CompensationStatus.CONFIRMED_TARGET, "confirmed $100k+", 10),
        ("80000", "120000", PayPeriod.YEAR, "USD", CompensationStatus.POSSIBLE_TARGET, "possible $100k+", 6),
        ("70000", "90000", PayPeriod.YEAR, "USD", CompensationStatus.BELOW_TARGET, "below target", 0),
        ("50", "60", PayPeriod.HOUR, "USD", CompensationStatus.CONFIRMED_TARGET, "confirmed $100k+", 10),
        ("90000", "120000", PayPeriod.YEAR, "EUR", CompensationStatus.UNRESOLVED, "unresolved", 3),
    ],
)
def test_compensation_labels_and_points(
    minimum, maximum, period, currency, status, label, points
):
    structured = SalaryRange(
        Decimal(minimum),
        Decimal(maximum),
        currency,
        period,
        FactSource.STRUCTURED_FEED,
    )
    result = assess_compensation(
        structured_salary=structured,
        parsed=ParsedCompensation.not_published(),
    )
    assert (result.status, result.label, result.points) == (status, label, points)


def test_absent_base_pay_is_unpublished():
    parsed = parse_compensation(
        split_job_sections("Benefits include equity and a performance bonus."),
        provenance=FactSource.OFFICIAL_DETAIL,
    )
    result = assess_compensation(structured_salary=None, parsed=parsed)
    assert parsed.status is CompensationParseStatus.NOT_PUBLISHED
    assert result.status is CompensationStatus.UNPUBLISHED
    assert (result.label, result.points) == ("not published", 3)


@pytest.mark.parametrize(
    "text",
    [
        "The base salary range is 100,000 to 140,000 per year.",
        "The base salary range is $8,000 to $10,000 per month.",
    ],
)
def test_malformed_base_pay_is_unresolved(text):
    parsed = parse_compensation(
        split_job_sections(text), provenance=FactSource.OFFICIAL_DETAIL
    )
    result = assess_compensation(structured_salary=None, parsed=parsed)
    assert parsed.status is CompensationParseStatus.UNRESOLVED
    assert result.status is CompensationStatus.UNRESOLVED
    assert (result.label, result.points) == ("unresolved", 3)


def test_official_base_pay_range_is_parsed():
    parsed = parse_compensation(
        split_job_sections("The annual base salary range is $105,000-$135,000."),
        provenance=FactSource.OFFICIAL_DETAIL,
    )
    assert parsed.status is CompensationParseStatus.PARSED
    assert parsed.salary == SalaryRange(
        Decimal("105000"),
        Decimal("135000"),
        "USD",
        PayPeriod.YEAR,
        FactSource.OFFICIAL_DETAIL,
    )


def test_structured_salary_precedes_malformed_prose():
    structured = SalaryRange(
        Decimal("110000"),
        Decimal("130000"),
        "USD",
        PayPeriod.YEAR,
        FactSource.STRUCTURED_FEED,
    )
    malformed = parse_compensation(
        split_job_sections("Base pay is 8,000 to 10,000 each month."),
        provenance=FactSource.OFFICIAL_DETAIL,
    )
    result = assess_compensation(structured_salary=structured, parsed=malformed)
    assert result.salary is structured
    assert result.status is CompensationStatus.CONFIRMED_TARGET


def test_non_usd_salary_preserves_original_evidence():
    parsed = parse_compensation(
        split_job_sections("Base salary is EUR 90,000 to EUR 120,000 per year."),
        provenance=FactSource.OFFICIAL_DETAIL,
    )
    result = assess_compensation(structured_salary=None, parsed=parsed)
    assert result.status is CompensationStatus.UNRESOLVED
    assert "EUR" in result.evidence
    assert result.points == 3


def test_bonus_equity_and_overtime_are_not_added_to_base_pay():
    parsed = parse_compensation(
        split_job_sections(
            "Base salary is $80,000-$90,000 per year, plus a $30,000 bonus, "
            "$50,000 equity award, and overtime."
        ),
        provenance=FactSource.OFFICIAL_DETAIL,
    )
    result = assess_compensation(structured_salary=None, parsed=parsed)
    assert result.status is CompensationStatus.BELOW_TARGET
    assert result.salary.maximum == Decimal("90000")


def test_nonofficial_salary_text_is_not_published_evidence():
    parsed = parse_compensation(
        split_job_sections("Base salary is $120,000-$140,000 per year."),
        provenance=FactSource.STRUCTURED_FEED,
    )
    assert parsed.status is CompensationParseStatus.NOT_PUBLISHED


@pytest.mark.parametrize(
    "source", [FactSource.UNAVAILABLE, FactSource.TRACKER_INFERENCE]
)
def test_salary_with_nonemployer_provenance_is_rejected(source):
    salary = SalaryRange(
        Decimal("120000"), Decimal("140000"), "USD", PayPeriod.YEAR, source
    )
    with pytest.raises(ValueError, match="provenance"):
        assess_compensation(
            structured_salary=salary,
            parsed=ParsedCompensation.not_published(),
        )
