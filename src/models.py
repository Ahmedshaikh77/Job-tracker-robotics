"""Typed domain models shared by the tracker pipeline."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from decimal import Decimal
from enum import StrEnum
import hashlib
import json
from types import MappingProxyType
from typing import Any, Mapping


def freeze_json_value(value: Any) -> Any:
    """Return an immutable, detached representation of a JSON-like value."""
    if isinstance(value, Mapping):
        return MappingProxyType(
            {str(key): freeze_json_value(item) for key, item in value.items()}
        )
    if isinstance(value, (list, tuple)):
        return tuple(freeze_json_value(item) for item in value)
    if isinstance(value, set):
        return frozenset(freeze_json_value(item) for item in value)
    return value


class FetchHealth(StrEnum):
    HEALTHY = "healthy"
    EMPTY_VALID = "empty-valid"
    PARTIAL = "partial"
    FAILED = "failed"


class CircuitState(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half-open"


class DetailStatus(StrEnum):
    HEALTHY = "healthy"
    CLOSED = "closed"
    FAILED = "failed"


class FactSource(StrEnum):
    STRUCTURED_FEED = "structured-feed"
    OFFICIAL_DETAIL = "official-detail"
    UNAVAILABLE = "unavailable"
    TRACKER_INFERENCE = "tracker-inference"


class EvidenceStatus(StrEnum):
    CONFIRMED = "Confirmed"
    NOT_PUBLISHED = "Not published"
    UNRESOLVED = "Unresolved"
    TRACKER_ASSESSMENT = "Tracker assessment"


class EmploymentType(StrEnum):
    FULL_TIME = "full-time"
    PART_TIME = "part-time"
    CONTRACT = "contract"
    TEMPORARY = "temporary"
    INTERNSHIP = "internship"
    OTHER = "other"
    UNKNOWN = "unknown"


class WorkplaceType(StrEnum):
    ONSITE = "onsite"
    HYBRID = "hybrid"
    REMOTE = "remote"
    UNKNOWN = "unknown"


class PayPeriod(StrEnum):
    YEAR = "year"
    HOUR = "hour"
    UNKNOWN = "unknown"


class AuthorizationStatus(StrEnum):
    CONFIRMED_SUPPORT = "confirmed-support"
    OPT_COMPATIBLE_UNCERTAIN = "opt-compatible-future-sponsorship-uncertain"
    UNKNOWN = "unknown"
    BLOCKED = "blocked"


class FreshnessStatus(StrEnum):
    RECENT = "recent"
    MATERIAL_REVISION = "material-revision"
    UNKNOWN_DATE_POST_SEED = "unknown-date-post-seed"
    STALE = "stale"


class CompensationStatus(StrEnum):
    CONFIRMED_TARGET = "confirmed-target"
    POSSIBLE_TARGET = "possible-target"
    BELOW_TARGET = "below-target"
    UNPUBLISHED = "unpublished"
    UNRESOLVED = "unresolved"


class Recommendation(StrEnum):
    APPLY_NOW = "Apply Now"
    STRONG = "Strong"
    MODERATE = "Moderate"
    SKIP = "Skip"


class QueueInvalidationReason(StrEnum):
    OFFICIAL_DETAIL_CLOSED = "official-detail-closed"
    COMPLETE_OMISSION_CLOSED = "complete-omission-closed"
    STAGE_ONE_REJECTED = "stage-one-rejected"
    ASSESSMENT_INELIGIBLE = "assessment-ineligible"
    DELIVERY_STALE = "delivery-stale"


@dataclass(frozen=True, slots=True)
class SalaryRange:
    minimum: Decimal | None = None
    maximum: Decimal | None = None
    currency: str = ""
    period: PayPeriod = PayPeriod.UNKNOWN
    source: FactSource = FactSource.UNAVAILABLE

    def _annualize(self, value: Decimal | None) -> Decimal | None:
        if value is None:
            return None
        if self.period is PayPeriod.HOUR:
            return value * Decimal("2080")
        if self.period is PayPeriod.YEAR:
            return value
        return None

    @property
    def annual_minimum(self) -> Decimal | None:
        return self._annualize(self.minimum)

    @property
    def annual_maximum(self) -> Decimal | None:
        return self._annualize(self.maximum)


@dataclass(frozen=True, slots=True)
class Job:
    source_type: str
    source_key: str
    company: str
    job_id: str
    title: str
    location: str
    url: str
    requisition_id: str = ""
    city: str = ""
    region: str = ""
    country_code: str = ""
    description: str = ""
    employment_type: EmploymentType = EmploymentType.UNKNOWN
    workplace_type: WorkplaceType = WorkplaceType.UNKNOWN
    posted_at: str | None = None
    updated_at: str | None = None
    salary: SalaryRange | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)
    provenance: Mapping[str, FactSource] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.source_key.startswith(f"{self.source_type}:"):
            raise ValueError("source_key must be the complete source record key")
        object.__setattr__(self, "metadata", freeze_json_value(self.metadata))
        object.__setattr__(self, "provenance", freeze_json_value(self.provenance))

    @property
    def key(self) -> str:
        return f"{self.source_key}:{self.job_id}"

    @property
    def content_hash(self) -> str:
        payload = {
            "title": self.title,
            "requisition_id": self.requisition_id,
            "location": self.location,
            "city": self.city,
            "region": self.region,
            "country_code": self.country_code,
            "url": self.url,
            "description": self.description,
            "employment_type": self.employment_type,
            "workplace_type": self.workplace_type,
            "posted_at": self.posted_at,
            "updated_at": self.updated_at,
            "salary": asdict(self.salary) if self.salary else None,
            "provenance": dict(self.provenance),
        }
        encoded = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


def _json_compatible(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _json_compatible(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_json_compatible(item) for item in value]
    if isinstance(value, (set, frozenset)):
        converted = [_json_compatible(item) for item in value]
        return sorted(converted, key=lambda item: json.dumps(item, sort_keys=True))
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, Decimal):
        return str(value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"value is not JSON-compatible: {type(value).__name__}")


def job_to_dict(job: Job) -> dict[str, Any]:
    salary = None
    if job.salary is not None:
        salary = {
            "minimum": str(job.salary.minimum)
            if job.salary.minimum is not None
            else None,
            "maximum": str(job.salary.maximum)
            if job.salary.maximum is not None
            else None,
            "currency": job.salary.currency,
            "period": job.salary.period.value,
            "source": job.salary.source.value,
        }
    return {
        "source_type": job.source_type,
        "source_key": job.source_key,
        "company": job.company,
        "job_id": job.job_id,
        "title": job.title,
        "location": job.location,
        "url": job.url,
        "requisition_id": job.requisition_id,
        "city": job.city,
        "region": job.region,
        "country_code": job.country_code,
        "description": job.description,
        "employment_type": job.employment_type.value,
        "workplace_type": job.workplace_type.value,
        "posted_at": job.posted_at,
        "updated_at": job.updated_at,
        "salary": salary,
        "metadata": _json_compatible(job.metadata),
        "provenance": {
            key: source.value for key, source in sorted(job.provenance.items())
        },
    }


def job_from_dict(payload: Mapping[str, Any]) -> Job:
    required = {
        "source_type",
        "source_key",
        "company",
        "job_id",
        "title",
        "location",
        "url",
    }
    allowed = required | {
        "requisition_id",
        "city",
        "region",
        "country_code",
        "description",
        "employment_type",
        "workplace_type",
        "posted_at",
        "updated_at",
        "salary",
        "metadata",
        "provenance",
    }
    unknown = set(payload) - allowed
    missing = required - set(payload)
    if unknown or missing:
        raise ValueError(
            f"invalid Job keys: missing={sorted(missing)}, unknown={sorted(unknown)}"
        )
    values = dict(payload)
    values["employment_type"] = EmploymentType(
        values.get("employment_type", EmploymentType.UNKNOWN.value)
    )
    values["workplace_type"] = WorkplaceType(
        values.get("workplace_type", WorkplaceType.UNKNOWN.value)
    )
    values["metadata"] = values.get("metadata", {})
    raw_provenance = values.get("provenance", {})
    if not isinstance(raw_provenance, Mapping):
        raise ValueError("invalid Job provenance")
    values["provenance"] = {
        str(key): FactSource(source) for key, source in raw_provenance.items()
    }
    raw_salary = values.get("salary")
    if raw_salary is not None:
        if not isinstance(raw_salary, Mapping):
            raise ValueError("invalid SalaryRange value")
        salary_keys = {"minimum", "maximum", "currency", "period", "source"}
        if set(raw_salary) != salary_keys:
            raise ValueError("invalid SalaryRange keys")
        values["salary"] = SalaryRange(
            minimum=Decimal(raw_salary["minimum"])
            if raw_salary["minimum"] is not None
            else None,
            maximum=Decimal(raw_salary["maximum"])
            if raw_salary["maximum"] is not None
            else None,
            currency=raw_salary["currency"],
            period=PayPeriod(raw_salary["period"]),
            source=FactSource(raw_salary["source"]),
        )
    return Job(**values)


@dataclass(frozen=True, slots=True)
class FetchContext:
    previous_etag: str | None = None
    previous_fingerprint: str | None = None
    previous_active_ids: frozenset[str] = frozenset()
    detail_retry_ids: frozenset[str] = frozenset()
    force_full: bool = False


@dataclass(frozen=True, slots=True)
class FetchResult:
    jobs: tuple[Job, ...]
    active_ids: frozenset[str]
    complete: bool
    source_total: int | None
    pages_fetched: int
    fetched_at: str
    health: FetchHealth
    total_is_authoritative: bool = False
    warnings: tuple[str, ...] = ()
    error: str | None = None
    etag: str | None = None
    fingerprint: str | None = None
    unchanged: bool = False

    def __post_init__(self) -> None:
        if self.pages_fetched < 0:
            raise ValueError("pages_fetched cannot be negative")
        if self.source_total is not None and self.source_total < 0:
            raise ValueError("source_total cannot be negative")
        if self.complete and not self.unchanged and not self.jobs:
            if (
                self.health is not FetchHealth.EMPTY_VALID
                or self.source_total != 0
                or not self.total_is_authoritative
            ):
                raise ValueError(
                    "complete empty FetchResult must be explicit empty-valid"
                )
        if self.total_is_authoritative and self.source_total is None:
            raise ValueError("authoritative total requires source_total")
        if self.health in {FetchHealth.HEALTHY, FetchHealth.EMPTY_VALID} and not self.complete:
            raise ValueError("healthy FetchResult must be complete")
        if self.health in {FetchHealth.PARTIAL, FetchHealth.FAILED} and self.complete:
            raise ValueError("partial or failed FetchResult cannot be complete")
        if self.unchanged and (
            not self.complete or self.health is not FetchHealth.HEALTHY
        ):
            raise ValueError("unchanged FetchResult must be complete and healthy")
        if self.complete and not self.unchanged:
            job_ids = frozenset(job.job_id for job in self.jobs)
            if job_ids != self.active_ids:
                raise ValueError("complete FetchResult jobs must equal active_ids")
            if len(job_ids) != len(self.jobs):
                raise ValueError("complete FetchResult cannot contain duplicate job IDs")
            if self.total_is_authoritative and self.source_total != len(self.active_ids):
                raise ValueError(
                    "authoritative source_total must equal complete active_ids"
                )
        if self.health is FetchHealth.EMPTY_VALID and (
            self.jobs
            or self.active_ids
            or self.source_total != 0
            or not self.total_is_authoritative
        ):
            raise ValueError(
                "empty-valid FetchResult requires explicit zero inventory"
            )


@dataclass(frozen=True, slots=True)
class DetailResult:
    job: Job | None
    status: DetailStatus
    fetched_at: str
    warnings: tuple[str, ...] = ()
    error: str | None = None

    def __post_init__(self) -> None:
        if self.status is DetailStatus.HEALTHY and self.job is None:
            raise ValueError("healthy detail requires a job")
        if self.status in {DetailStatus.CLOSED, DetailStatus.FAILED} and self.job is not None:
            raise ValueError("closed or failed detail cannot carry a job")


@dataclass(frozen=True, slots=True)
class ExperienceRequirement:
    stated_required_minimum: float | None
    stated_required_maximum: float | None
    effective_required_minimum: float | None
    effective_required_maximum: float | None
    preferred_minimum: float | None
    preferred_maximum: float | None
    flexible: bool
    flexibility_basis: str
    unresolved: bool
    early_career_supported: bool
    evidence: str
    source: FactSource


@dataclass(frozen=True, slots=True)
class AuthorizationAssessment:
    status: AuthorizationStatus
    evidence: str
    source: FactSource


@dataclass(frozen=True, slots=True)
class FreshnessAssessment:
    status: FreshnessStatus
    eligible: bool
    age_days: int | None
    evidence: str
    source: FactSource


@dataclass(frozen=True, slots=True)
class CompensationAssessment:
    status: CompensationStatus
    label: str
    points: int
    salary: SalaryRange | None
    evidence: str
    source: FactSource


@dataclass(frozen=True, slots=True)
class QualificationAssessment:
    required_groups: tuple[str, ...]
    matched_required_groups: tuple[str, ...]
    missing_required_groups: tuple[str, ...]
    preferred_gaps: tuple[str, ...]
    points: int


@dataclass(frozen=True, slots=True)
class ScoreBreakdown:
    scoring_version: int
    role_alignment: int
    technical_evidence: int
    experience_fit: int
    domain_alignment: int
    qualification_coverage: int
    authorization: int
    compensation: int
    recency: int

    def __post_init__(self) -> None:
        values_and_caps = (
            (self.role_alignment, 25),
            (self.technical_evidence, 15),
            (self.experience_fit, 15),
            (self.domain_alignment, 10),
            (self.qualification_coverage, 10),
            (self.authorization, 10),
            (self.compensation, 10),
            (self.recency, 5),
        )
        if self.scoring_version != 1:
            raise ValueError("unsupported scoring version")
        if any(value < 0 or value > cap for value, cap in values_and_caps):
            raise ValueError("score component is outside its configured cap")

    @property
    def total(self) -> int:
        return sum(
            (
                self.role_alignment,
                self.technical_evidence,
                self.experience_fit,
                self.domain_alignment,
                self.qualification_coverage,
                self.authorization,
                self.compensation,
                self.recency,
            )
        )


@dataclass(frozen=True, slots=True)
class ScoringResult:
    breakdown: ScoreBreakdown
    recommendation: Recommendation
    match_reason: str
    important_gap: str

    @property
    def score(self) -> int:
        return self.breakdown.total


@dataclass(frozen=True, slots=True)
class JobAssessment:
    job: Job
    candidate_id: str
    reopen_generation: int
    eligible: bool
    score: int
    recommendation: Recommendation
    role_family: str | None
    match_reason: str
    important_gap: str
    resume_filename: str | None
    resume_reason: str
    experience: ExperienceRequirement
    authorization: AuthorizationAssessment
    freshness: FreshnessAssessment
    compensation: CompensationAssessment
    qualification: QualificationAssessment
    score_breakdown: ScoreBreakdown
    hard_blocks: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.candidate_id or self.reopen_generation < 0:
            raise ValueError("assessment identity and reopen generation must be valid")
        if self.score != self.score_breakdown.total:
            raise ValueError("assessment score must equal score breakdown total")
        blocked = (
            bool(self.hard_blocks)
            or self.authorization.status is AuthorizationStatus.BLOCKED
        )
        structurally_ineligible = (
            self.job.employment_type is not EmploymentType.FULL_TIME
            or not self.freshness.eligible
            or not self.role_family
            or not self.resume_filename
        )
        if self.eligible and (
            blocked
            or structurally_ineligible
            or self.recommendation is Recommendation.SKIP
        ):
            raise ValueError("eligible assessment contradicts its gates")
        if not self.eligible and self.recommendation is not Recommendation.SKIP:
            raise ValueError("ineligible assessment must be Skip")

    @property
    def alert_basis_hash(self) -> str:
        payload = {
            "scoring_version": self.score_breakdown.scoring_version,
            "recommendation": self.recommendation,
            "title": self.job.title,
            "employment_type": self.job.employment_type,
            "location": (
                self.job.city,
                self.job.region,
                self.job.country_code,
                self.job.location,
            ),
            "experience": {
                "stated_min": self.experience.stated_required_minimum,
                "stated_max": self.experience.stated_required_maximum,
                "effective_min": self.experience.effective_required_minimum,
                "effective_max": self.experience.effective_required_maximum,
                "preferred_min": self.experience.preferred_minimum,
                "preferred_max": self.experience.preferred_maximum,
                "flexible": self.experience.flexible,
                "unresolved": self.experience.unresolved,
            },
            "authorization": self.authorization.status,
            "compensation": {
                "status": self.compensation.status,
                "salary": asdict(self.compensation.salary)
                if self.compensation.salary
                else None,
            },
            "resume_filename": self.resume_filename,
        }
        encoded = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @property
    def revision_id(self) -> str:
        return (
            f"{self.candidate_id}:{self.reopen_generation}:{self.alert_basis_hash}"
        )


@dataclass(frozen=True, slots=True)
class AlertFact:
    value: str
    status: EvidenceStatus
    provenance: FactSource


@dataclass(frozen=True, slots=True)
class AlertItem:
    revision_id: str
    candidate_id: str
    reopen_generation: int
    identity_aliases: tuple[str, ...]
    source_key: str
    company: str
    title: str
    application_url: str
    location: AlertFact
    work_arrangement: AlertFact
    posted_date: AlertFact
    first_seen_at: str
    salary: AlertFact
    experience: AlertFact
    authorization: AlertFact
    full_time: AlertFact
    score: int
    recommendation: Recommendation
    match_reason: str
    important_gap: str
    resume_filename: str
    role_family: str
    resume_reason: str
    queued_at: str
    queued_run_id: str
    fetch_completed_at: str


@dataclass(frozen=True, slots=True)
class PendingHealthSummary:
    delivery_id: str
    local_date: str
    text: str
    queued_at: str
