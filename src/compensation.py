"""Parse and assess comparable base compensation."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
import re

from .models import (
    CompensationAssessment,
    CompensationStatus,
    FactSource,
    PayPeriod,
    SalaryRange,
)
from .parsing import JobSections


class CompensationParseStatus(StrEnum):
    NOT_PUBLISHED = "not-published"
    PARSED = "parsed"
    UNRESOLVED = "unresolved"


@dataclass(frozen=True, slots=True)
class ParsedCompensation:
    status: CompensationParseStatus
    salary: SalaryRange | None
    evidence: str
    source: FactSource

    @classmethod
    def not_published(cls) -> "ParsedCompensation":
        return cls(
            CompensationParseStatus.NOT_PUBLISHED,
            None,
            "No base compensation published",
            FactSource.UNAVAILABLE,
        )


_BASE_RE = re.compile(r"\b(?:annual\s+)?base (?:salary|pay)|\bsalary range\b", re.I)
_RANGE_RE = re.compile(
    r"(?P<c1>USD|EUR|GBP|\$|\u20ac|\u00a3)\s*"
    r"(?P<minimum>\d[\d,]*(?:\.\d+)?\s*[kK]?)\s*"
    r"(?:-|\u2013|\u2014|to)\s*"
    r"(?:(?P<c2>USD|EUR|GBP|\$|\u20ac|\u00a3)\s*)?"
    r"(?P<maximum>\d[\d,]*(?:\.\d+)?\s*[kK]?)",
    re.I,
)
_CURRENCY = {"$": "USD", "USD": "USD", "\u20ac": "EUR", "EUR": "EUR", "\u00a3": "GBP", "GBP": "GBP"}


def _amount(raw: str) -> Decimal:
    cleaned = raw.replace(",", "").replace(" ", "")
    multiplier = Decimal("1000") if cleaned[-1:].casefold() == "k" else Decimal("1")
    if multiplier != 1:
        cleaned = cleaned[:-1]
    return Decimal(cleaned) * multiplier


def _period(sentence: str) -> PayPeriod:
    lower = sentence.casefold()
    if re.search(r"\b(?:annual|annually|per year|a year|yearly)\b", lower):
        return PayPeriod.YEAR
    if re.search(r"\b(?:per hour|an hour|hourly)\b", lower):
        return PayPeriod.HOUR
    return PayPeriod.UNKNOWN


def parse_compensation(
    sections: JobSections,
    *,
    provenance: FactSource,
) -> ParsedCompensation:
    """Parse an explicit base range from official detail text only."""
    if provenance is not FactSource.OFFICIAL_DETAIL:
        return ParsedCompensation.not_published()
    sentences = sections.required + sections.responsibilities + sections.preferred + sections.other
    for sentence in sentences:
        if not _BASE_RE.search(sentence):
            continue
        base_clause = re.split(r"\b(?:plus|bonus|equity|overtime|one-time)\b", sentence, maxsplit=1, flags=re.I)[0]
        match = _RANGE_RE.search(base_clause)
        period = _period(base_clause)
        if not match or period is PayPeriod.UNKNOWN:
            return ParsedCompensation(
                CompensationParseStatus.UNRESOLVED,
                None,
                sentence,
                FactSource.OFFICIAL_DETAIL,
            )
        currency_one = _CURRENCY[match.group("c1").upper() if match.group("c1").isalpha() else match.group("c1")]
        if match.group("c2"):
            raw_two = match.group("c2")
            currency_two = _CURRENCY[raw_two.upper() if raw_two.isalpha() else raw_two]
            if currency_one != currency_two:
                return ParsedCompensation(
                    CompensationParseStatus.UNRESOLVED,
                    None,
                    sentence,
                    FactSource.OFFICIAL_DETAIL,
                )
        salary = SalaryRange(
            _amount(match.group("minimum")),
            _amount(match.group("maximum")),
            currency_one,
            period,
            FactSource.OFFICIAL_DETAIL,
        )
        return ParsedCompensation(
            CompensationParseStatus.PARSED,
            salary,
            sentence,
            FactSource.OFFICIAL_DETAIL,
        )
    return ParsedCompensation.not_published()


def _salary_evidence(salary: SalaryRange) -> str:
    low = str(salary.minimum) if salary.minimum is not None else "unknown"
    high = str(salary.maximum) if salary.maximum is not None else "unknown"
    return f"{salary.currency} {low}-{high} per {salary.period.value}"


def assess_compensation(
    *,
    structured_salary: SalaryRange | None,
    parsed: ParsedCompensation,
) -> CompensationAssessment:
    """Score structured salary first, then an official parsed base range."""
    salary = structured_salary or parsed.salary
    if salary is not None and salary.source not in {
        FactSource.STRUCTURED_FEED,
        FactSource.OFFICIAL_DETAIL,
    }:
        raise ValueError("salary provenance must be employer-provided")
    if salary is None:
        unresolved = parsed.status is CompensationParseStatus.UNRESOLVED
        return CompensationAssessment(
            CompensationStatus.UNRESOLVED if unresolved else CompensationStatus.UNPUBLISHED,
            "unresolved" if unresolved else "not published",
            3,
            None,
            parsed.evidence,
            parsed.source if unresolved else FactSource.UNAVAILABLE,
        )

    evidence = _salary_evidence(salary)
    if salary.currency.upper() != "USD":
        return CompensationAssessment(
            CompensationStatus.UNRESOLVED,
            "unresolved",
            3,
            salary,
            evidence,
            salary.source,
        )
    minimum = salary.annual_minimum
    maximum = salary.annual_maximum
    if minimum is None and maximum is None:
        return CompensationAssessment(
            CompensationStatus.UNRESOLVED,
            "unresolved",
            3,
            salary,
            evidence,
            salary.source,
        )
    target = Decimal("100000")
    if minimum is not None and minimum >= target:
        status, label, points = CompensationStatus.CONFIRMED_TARGET, "confirmed $100k+", 10
    elif maximum is not None and maximum >= target:
        status, label, points = CompensationStatus.POSSIBLE_TARGET, "possible $100k+", 6
    else:
        status, label, points = CompensationStatus.BELOW_TARGET, "below target", 0
    return CompensationAssessment(
        status,
        label,
        points,
        salary,
        evidence,
        salary.source,
    )
