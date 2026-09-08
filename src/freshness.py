"""Authoritative posting freshness and material revision assessment."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from .dedupe import normalize_text
from .lifecycle import RevisionPolicy, comparison_basis
from .models import (
    AuthorizationAssessment,
    CompensationAssessment,
    ExperienceRequirement,
    FactSource,
    FreshnessAssessment,
    FreshnessStatus,
    Job,
)


@dataclass(frozen=True, slots=True)
class MaterialRevision:
    changed_fields: tuple[str, ...]
    first_observed_at: str | None

    @property
    def is_material(self) -> bool:
        return bool(self.changed_fields)


def _experience_snapshot(experience: ExperienceRequirement) -> dict[str, Any]:
    return {
        "stated_minimum": experience.stated_required_minimum,
        "stated_maximum": experience.stated_required_maximum,
        "effective_minimum": experience.effective_required_minimum,
        "effective_maximum": experience.effective_required_maximum,
        "preferred_minimum": experience.preferred_minimum,
        "preferred_maximum": experience.preferred_maximum,
        "flexible": experience.flexible,
        "unresolved": experience.unresolved,
    }


def _location_snapshot(job: Job) -> dict[str, str]:
    return {
        "raw": normalize_text(job.location),
        "city": normalize_text(job.city),
        "region": normalize_text(job.region),
        "country_code": normalize_text(job.country_code),
    }


def _normalise_old_location(value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping):
        return {"raw": "", "city": "", "region": "", "country_code": ""}
    return {
        key: normalize_text(str(value.get(key, "")))
        for key in ("raw", "city", "region", "country_code")
    }


def _salary_snapshot(compensation: CompensationAssessment) -> dict[str, str | None] | None:
    salary = compensation.salary
    if salary is None:
        return None
    return {
        "minimum": str(salary.annual_minimum) if salary.annual_minimum is not None else None,
        "maximum": str(salary.annual_maximum) if salary.annual_maximum is not None else None,
        "currency": salary.currency.upper(),
    }


def _decimal(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except Exception:
        return None


def _target_relation(salary: Mapping[str, Any], threshold: Decimal) -> str:
    minimum = _decimal(salary.get("minimum"))
    maximum = _decimal(salary.get("maximum"))
    if minimum is not None and minimum >= threshold:
        return "confirmed"
    if maximum is not None and maximum >= threshold:
        return "possible"
    return "below"


def _compensation_material(
    previous: Any,
    current: dict[str, str | None] | None,
    policy: RevisionPolicy,
) -> bool:
    if previous is None or current is None:
        return previous != current
    if not isinstance(previous, Mapping):
        return True
    if str(previous.get("currency", "")).upper() != current.get("currency"):
        return True
    currency = str(current.get("currency", "")).upper()
    if currency == "USD" and _target_relation(
        previous, policy.compensation_threshold
    ) != _target_relation(current, policy.compensation_threshold):
        return True
    for field in ("minimum", "maximum"):
        old = _decimal(previous.get(field))
        new = _decimal(current.get(field))
        if old is None or new is None:
            if old != new:
                return True
        elif old == 0:
            if new != 0:
                return True
        elif abs(new - old) / abs(old) >= policy.compensation_material_change_ratio:
            return True
    return False


def assess_material_revision(
    job: Job,
    experience: ExperienceRequirement,
    authorization: AuthorizationAssessment,
    compensation: CompensationAssessment,
    candidate_state: Mapping[str, Any],
    *,
    policy: RevisionPolicy,
) -> MaterialRevision:
    """Compare current eligibility facts with the shared durable comparison basis."""
    basis = comparison_basis(candidate_state)
    if not isinstance(basis, Mapping):
        return MaterialRevision((), None)

    changed: list[str] = []
    if normalize_text(job.title) != normalize_text(str(basis.get("title", ""))):
        changed.append("title")
    if job.employment_type.value != basis.get("employment_type"):
        changed.append("employment_type")
    if _experience_snapshot(experience) != basis.get("required_experience"):
        changed.append("required_experience")
    if authorization.status.value != basis.get("authorization"):
        changed.append("authorization")
    if _location_snapshot(job) != _normalise_old_location(basis.get("location")):
        changed.append("location")
    if _compensation_material(basis.get("salary"), _salary_snapshot(compensation), policy):
        changed.append("compensation")
    return MaterialRevision(
        tuple(changed),
        str(candidate_state.get("last_seen_at")) if changed and candidate_state.get("last_seen_at") else None,
    )


def _source_date(value: str, timezone: ZoneInfo) -> date | None:
    try:
        if len(value) == 10:
            return date.fromisoformat(value)
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return parsed.date()
    return parsed.astimezone(timezone).date()


def assess_freshness(
    job: Job,
    candidate_state: Mapping[str, Any],
    *,
    material_revision: MaterialRevision,
    now: datetime,
    freshness_days: int = 30,
) -> FreshnessAssessment:
    """Assess freshness by New York calendar date, never by update timestamp."""
    if now.tzinfo is None:
        raise ValueError("now must be timezone aware")
    if type(freshness_days) is not int or freshness_days < 0:
        raise ValueError("freshness_days must be a non-negative integer")
    new_york = ZoneInfo("America/New_York")
    today = now.astimezone(new_york).date()
    source = job.provenance.get("posted_at", FactSource.UNAVAILABLE)
    authoritative = source in {FactSource.STRUCTURED_FEED, FactSource.OFFICIAL_DETAIL}
    posted = _source_date(job.posted_at, new_york) if authoritative and job.posted_at else None
    if posted is not None:
        age_days = (today - posted).days
        if posted >= today - timedelta(days=freshness_days):
            return FreshnessAssessment(
                FreshnessStatus.RECENT,
                True,
                age_days,
                f"Authoritative posted date {posted.isoformat()}",
                source,
            )
        if material_revision.is_material:
            return FreshnessAssessment(
                FreshnessStatus.MATERIAL_REVISION,
                True,
                age_days,
                "Official material revision: " + ", ".join(material_revision.changed_fields),
                FactSource.TRACKER_INFERENCE,
            )
        return FreshnessAssessment(
            FreshnessStatus.STALE,
            False,
            age_days,
            f"Authoritative posted date {posted.isoformat()} is outside the freshness window",
            source,
        )

    if not candidate_state.get("discovered_during_seed", False):
        return FreshnessAssessment(
            FreshnessStatus.UNKNOWN_DATE_POST_SEED,
            True,
            None,
            "Posted date unavailable; first discovered after seed",
            FactSource.TRACKER_INFERENCE,
        )
    return FreshnessAssessment(
        FreshnessStatus.STALE,
        False,
        None,
        "Posted date unavailable for a seeded candidate",
        FactSource.UNAVAILABLE,
    )
