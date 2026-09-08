# Job Tracker Core Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the typed fetch contract, resilient HTTP layer, version-2 state store, lifecycle rules, circuit breaker, and health reporting that every later tracker phase uses.

**Architecture:** Keep the existing Python package and GitHub-backed JSON state, but replace bare job lists and loosely structured state with explicit dataclasses and transactional state transitions. Fetchers receive compact prior-source context, return complete or incomplete snapshots truthfully, and state changes are written atomically.

**Tech Stack:** Python 3.11, dataclasses, requests, PyYAML, pytest, unittest.mock

**Spec:** `docs/superpowers/specs/2026-09-07-job-tracker-alerts-v2-design.md`

## Global Constraints

- Fetch health values are exactly `healthy`, `empty-valid`, `partial`, and `failed`; circuit states are exactly `closed`, `open`, and `half-open`.
- A circuit opens after three consecutive failed or incomplete fetches and receives one half-open probe every 24 hours.
- A source collapse below 40 percent of the preceding complete count is anomalous when the preceding count was at least 20, unless an authoritative total confirms the decrease.
- Closure requires two consecutive complete omissions or an official detail response of 404 or 410. A 403, partial response, unexplained empty response, or anomalous collapse never closes jobs.
- Persist no full job descriptions. Keep closed candidates for 90 days, delivered revisions for 365 days with a 10,000-entry cap, still-open pending alerts for at most 30 days, digest entries for 30 days, and 30 health events per source. Immediately invalidate queued alerts when the role closes or becomes ineligible.
- Invalid JSON is a hard failure. Production state writes are atomic. Dry-run and validation modes never mutate production state.
- Do not send Telegram messages or call live company endpoints while implementing this phase.

---

### Task 1: Establish the test harness and typed domain model

**Files:**
- Modify: `requirements.txt`
- Create: `pytest.ini`
- Modify: `src/models.py:1-25`
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`
- Create: `tests/test_models.py`

**Interfaces:**
- Consumes: no new project interfaces
- Produces: `FetchHealth`, `CircuitState`, `DetailStatus`, `FactSource`, `EvidenceStatus`, `EmploymentType`, `WorkplaceType`, `PayPeriod`, `AuthorizationStatus`, `FreshnessStatus`, `CompensationStatus`, `Recommendation`, `QueueInvalidationReason`, `SalaryRange`, `Job`, `job_to_dict()`, `job_from_dict()`, `FetchContext`, `FetchResult`, `DetailResult`, `ExperienceRequirement`, `AuthorizationAssessment`, `FreshnessAssessment`, `CompensationAssessment`, `QualificationAssessment`, `ScoreBreakdown`, `ScoringResult`, `JobAssessment`, `AlertFact`, `AlertItem`, and `PendingHealthSummary`

- [ ] **Step 1: Pin runtime and test dependencies**

Replace `requirements.txt` with:

```text
requests==2.34.2
PyYAML==6.0.3
pytest==9.0.2
```

Create `pytest.ini`:

```ini
[pytest]
testpaths = tests
addopts = -ra
```

Create an empty `tests/__init__.py` so shared fixtures can be imported unambiguously as `tests.fakes`.

Create `tests/conftest.py` with an autouse network guard and reusable model factories:

```python
import pytest
import requests

from src.models import EmploymentType, FetchContext, Job


@pytest.fixture(autouse=True)
def block_unmocked_network(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("unmocked network access is forbidden in tests")
    monkeypatch.setattr(requests.sessions.Session, "request", blocked)


@pytest.fixture
def make_job():
    def factory(**overrides):
        values = {
            "source_type": "greenhouse",
            "source_key": "greenhouse:figureai",
            "company": "Figure",
            "job_id": "123",
            "title": "Robotics Test Engineer",
            "location": "Sunnyvale, CA",
            "url": "https://example.test/jobs/123",
            "description": "Build validation fixtures.",
            "employment_type": EmploymentType.FULL_TIME,
            "country_code": "US",
        }
        values.update(overrides)
        return Job(**values)
    return factory


@pytest.fixture
def fetch_context():
    return FetchContext()
```

Install the exact dependencies:

```bash
python -m pip install -r requirements.txt
```

- [ ] **Step 2: Write the failing model tests**

Create `tests/test_models.py` with tests that exercise stable identity, revision hashing, annual salary conversion, and fetch-result validation:

```python
from decimal import Decimal

import pytest

from src.models import AlertItem, EmploymentType, FetchHealth, FetchResult, Job, PayPeriod, SalaryRange


def make_job(**overrides):
    values = {
        "source_type": "greenhouse",
        "source_key": "greenhouse:figureai",
        "company": "Figure",
        "job_id": "123",
        "title": "Robotics Test Engineer",
        "location": "Sunnyvale, CA",
        "url": "https://example.test/jobs/123",
        "description": "Build validation fixtures.",
        "employment_type": EmploymentType.FULL_TIME,
    }
    values.update(overrides)
    return Job(**values)


def test_job_key_is_source_scoped_and_revision_changes_with_content():
    original = make_job()
    unchanged = make_job()
    changed = make_job(description="Build and automate validation fixtures.")

    assert original.key == "greenhouse:figureai:123"
    assert original.content_hash == unchanged.content_hash
    assert original.content_hash != changed.content_hash


def test_same_board_slug_and_id_under_different_fetchers_cannot_collide():
    first = make_job(source_type="greenhouse", source_key="greenhouse:shared")
    second = make_job(source_type="ashby", source_key="ashby:shared")
    assert first.key != second.key


def test_hourly_salary_annualizes_using_2080_hours():
    salary = SalaryRange(
        minimum=Decimal("50"),
        maximum=Decimal("60"),
        currency="USD",
        period=PayPeriod.HOUR,
    )

    assert salary.annual_minimum == Decimal("104000")
    assert salary.annual_maximum == Decimal("124800")


def test_unknown_pay_period_or_currency_is_not_assumed_annual_usd():
    salary = SalaryRange(minimum=Decimal("120000"))
    assert salary.currency == ""
    assert salary.annual_minimum is None


def test_complete_result_requires_active_ids():
    with pytest.raises(ValueError, match="active_ids"):
        FetchResult(
            jobs=(make_job(),),
            active_ids=frozenset(),
            complete=True,
            source_total=1,
            pages_fetched=1,
            fetched_at="2026-09-07T12:00:00+00:00",
            health=FetchHealth.HEALTHY,
        )


def test_healthy_unexplained_empty_is_invalid():
    with pytest.raises(ValueError, match="empty-valid"):
        FetchResult(
            jobs=(),
            active_ids=frozenset(),
            complete=True,
            source_total=None,
            pages_fetched=1,
            fetched_at="2026-09-07T12:00:00+00:00",
            health=FetchHealth.HEALTHY,
        )


def test_alert_item_contract_cannot_store_description():
    assert "description" not in AlertItem.__dataclass_fields__
```

Also construct a `JobAssessment` with a score that differs from `score_breakdown.total` and assert it raises `ValueError`; downstream formatting must never display an internally inconsistent fit score. Add negative tests for a frozen `Job` whose input metadata/provenance dictionaries are mutated afterward, every impossible `FetchResult` health/completeness/authoritative-total combination, every impossible `DetailResult` status/job combination, negative or over-cap scoring components, and an assessment marked eligible while blocked, stale, non-full-time, unmapped to a resume, or recommended Skip.

Create two version-1 assessments for the same job content and reopen generation but different recommendation/material scoring bases. Assert their `revision_id` values differ. Create a description-only evidence wording change with identical canonical material fields and assert the revision ID stays stable; lifecycle still decides whether any changed basis is alertable. Build two Jobs from provenance dictionaries with opposite insertion order and assert equal content hashes. Add and use explicit `job_to_dict()`/`job_from_dict()` helpers instead of `dataclasses.asdict(job)`, because immutable mapping wrappers must be converted to ordinary sorted dictionaries at the serialization boundary.

- [ ] **Step 3: Run the model tests and verify the expected failure**

Run:

```bash
python -m pytest tests/test_models.py -v
```

Expected: collection fails because the new model types and fields do not exist.

- [ ] **Step 4: Implement the complete model contract**

Replace `src/models.py` with dataclasses following these exact public fields and properties:

```python
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from decimal import Decimal
from enum import StrEnum
import hashlib
import json
from types import MappingProxyType
from typing import Any, Mapping


def freeze_json_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(k): freeze_json_value(v) for k, v in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(freeze_json_value(v) for v in value)
    if isinstance(value, set):
        return frozenset(freeze_json_value(v) for v in value)
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
            "minimum": str(job.salary.minimum) if job.salary.minimum is not None else None,
            "maximum": str(job.salary.maximum) if job.salary.maximum is not None else None,
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
    required = {"source_type", "source_key", "company", "job_id", "title", "location", "url"}
    allowed = required | {
        "requisition_id", "city", "region", "country_code", "description",
        "employment_type", "workplace_type", "posted_at", "updated_at",
        "salary", "metadata", "provenance",
    }
    unknown = set(payload) - allowed
    missing = required - set(payload)
    if unknown or missing:
        raise ValueError(f"invalid Job keys: missing={sorted(missing)}, unknown={sorted(unknown)}")
    values = dict(payload)
    values["employment_type"] = EmploymentType(values.get("employment_type", "unknown"))
    values["workplace_type"] = WorkplaceType(values.get("workplace_type", "unknown"))
    values["metadata"] = values.get("metadata", {})
    values["provenance"] = {
        str(key): FactSource(source)
        for key, source in values.get("provenance", {}).items()
    }
    raw_salary = values.get("salary")
    if raw_salary is not None:
        salary_keys = {"minimum", "maximum", "currency", "period", "source"}
        if set(raw_salary) != salary_keys:
            raise ValueError("invalid SalaryRange keys")
        values["salary"] = SalaryRange(
            minimum=Decimal(raw_salary["minimum"]) if raw_salary["minimum"] is not None else None,
            maximum=Decimal(raw_salary["maximum"]) if raw_salary["maximum"] is not None else None,
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
        if self.total_is_authoritative and self.source_total is None:
            raise ValueError("authoritative total requires source_total")
        if self.health in {FetchHealth.HEALTHY, FetchHealth.EMPTY_VALID} and not self.complete:
            raise ValueError("healthy FetchResult must be complete")
        if self.health in {FetchHealth.PARTIAL, FetchHealth.FAILED} and self.complete:
            raise ValueError("partial or failed FetchResult cannot be complete")
        if self.unchanged and (not self.complete or self.health is not FetchHealth.HEALTHY):
            raise ValueError("unchanged FetchResult must be complete and healthy")
        if self.complete and not self.unchanged:
            job_ids = frozenset(job.job_id for job in self.jobs)
            if job_ids != self.active_ids:
                raise ValueError("complete FetchResult jobs must equal active_ids")
            if len(job_ids) != len(self.jobs):
                raise ValueError("complete FetchResult cannot contain duplicate job IDs")
            if self.total_is_authoritative and self.source_total != len(self.active_ids):
                raise ValueError("authoritative source_total must equal complete active_ids")
        if self.complete and not self.unchanged and not self.jobs:
            if (
                self.health is not FetchHealth.EMPTY_VALID
                or self.source_total != 0
                or not self.total_is_authoritative
            ):
                raise ValueError("complete empty FetchResult must be explicit empty-valid")
        if self.health is FetchHealth.EMPTY_VALID and (
            self.jobs or self.active_ids or self.source_total != 0 or not self.total_is_authoritative
        ):
            raise ValueError("empty-valid FetchResult requires explicit zero inventory")


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
        return sum((
            self.role_alignment,
            self.technical_evidence,
            self.experience_fit,
            self.domain_alignment,
            self.qualification_coverage,
            self.authorization,
            self.compensation,
            self.recency,
        )))


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
        blocked = bool(self.hard_blocks) or self.authorization.status is AuthorizationStatus.BLOCKED
        structurally_ineligible = (
            self.job.employment_type is not EmploymentType.FULL_TIME
            or not self.freshness.eligible
            or not self.role_family
            or not self.resume_filename
        )
        if self.eligible and (blocked or structurally_ineligible or self.recommendation is Recommendation.SKIP):
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
            "location": (self.job.city, self.job.region, self.job.country_code, self.job.location),
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
                "salary": asdict(self.compensation.salary) if self.compensation.salary else None,
            },
            "resume_filename": self.resume_filename,
        }
        encoded = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @property
    def revision_id(self) -> str:
        return f"{self.candidate_id}:{self.reopen_generation}:{self.alert_basis_hash}"


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
```

`AlertItem.identity_aliases` is a sorted immutable tuple containing only the candidate's source-scoped and trusted employer-requisition aliases. It excludes URL-only aliases so a closed repost at the same URL can receive a new identity. `source_key` is the complete stable source record key and supports sanitized health/quarantine reporting. Serialization and projection tests must prove both fields survive queue, save, reload, and delivery receipt creation unchanged.

`job_to_dict()` names every public `Job` field, converts immutable metadata/provenance recursively to ordinary JSON-compatible dictionaries/lists, encodes enums by value, and encodes salary amounts as decimal strings. `job_from_dict()` rejects unknown or missing required keys, reconstructs `Decimal`, enums, provenance, and `SalaryRange`, then lets `Job.__post_init__()` freeze the result. Never call `asdict(job)` on mapping proxies. Add an exact round-trip test and an insertion-order-independent content-hash test.

- [ ] **Step 5: Run the focused tests**

Run:

```bash
python -m pytest tests/test_models.py -v
```

Expected: all model tests pass.

- [ ] **Step 6: Commit the model foundation**

```bash
git add requirements.txt pytest.ini src/models.py tests/__init__.py tests/conftest.py tests/test_models.py
git commit -m "refactor: define tracker domain models"
```

---

### Task 2: Add the resilient HTTP client and fetcher contract

**Files:**
- Create: `src/fetchers/http.py`
- Modify: `src/fetchers/base.py:1-57`
- Create: `tests/fakes.py`
- Create: `tests/test_http_client.py`
- Create: `tests/test_fetcher_base.py`

**Interfaces:**
- Consumes: `FetchContext`, `FetchResult`, and `Job` from Task 1
- Produces: `JsonResponse`, `TextResponse`, `RetryPolicy`, shared `HostPacer`, `HttpClient.get_json()`, `HttpClient.get_text()`, `HttpClient.post_json()`, `Fetcher.fetch(company, context)`, `Fetcher.fetch_detail(company, job)`, and `source_key(company)`

- [ ] **Step 1: Write failing retry, 304, and sanitization tests**

Create deterministic fake response/session objects in `tests/test_http_client.py`, then assert these behaviors:

```python
from unittest.mock import Mock

import requests

from src.fetchers.http import HttpClient, RetryPolicy


def response(status, payload=None, headers=None):
    item = Mock()
    item.status_code = status
    item.headers = headers or {}
    item.json.return_value = payload
    item.raise_for_status.side_effect = (
        requests.HTTPError(response=item) if status >= 400 else None
    )
    return item


def test_get_retries_429_using_retry_after_then_succeeds():
    session = Mock()
    session.get.side_effect = [
        response(429, headers={"Retry-After": "3"}),
        response(200, {"jobs": []}, {"ETag": '"abc"'}),
    ]
    sleeps = []
    client = HttpClient(
        session=session,
        retry=RetryPolicy(attempts=3, backoff_base=2, backoff_cap=60),
        sleep=sleeps.append,
        jitter=lambda: 0,
    )

    result = client.get_json("https://example.test/jobs")

    assert result.data == {"jobs": []}
    assert result.etag == '"abc"'
    assert sleeps == [3]
    assert session.get.call_count == 2


def test_get_returns_not_modified_without_decoding_json():
    session = Mock()
    session.get.return_value = response(304, headers={"ETag": '"abc"'})
    client = HttpClient(session=session, sleep=lambda seconds: None, jitter=lambda: 0)

    result = client.get_json("https://example.test/jobs", etag='"abc"')

    assert result.not_modified is True
    session.get.return_value.json.assert_not_called()


def test_error_text_does_not_include_token_bearing_url():
    session = Mock()
    session.get.return_value = response(401)
    client = HttpClient(session=session, sleep=lambda seconds: None, jitter=lambda: 0)

    try:
        client.get_json("https://example.test/botSECRET/getMe")
    except requests.HTTPError as exc:
        assert "SECRET" not in str(exc)
```

Create `tests/test_fetcher_base.py` to assert that an unimplemented fetcher cannot be instantiated and that the base `fetch_detail()` fails closed. Name the regression `test_default_detail_cannot_verify_job`; it must assert `DetailStatus.FAILED` and `job is None`. No inherited default may certify a listing record as official detail.

Add `test_host_pacing_is_shared_across_clients` using two clients, the same hostname, an injected monotonic clock, and an injected sleeper; assert the second client waits so the host interval is at least 0.25 seconds.

- [ ] **Step 2: Run the tests and verify the expected import failures**

```bash
python -m pytest tests/test_http_client.py tests/test_fetcher_base.py -v
```

Expected: tests fail because `src.fetchers.http` and the new base signatures do not exist.

- [ ] **Step 3: Implement `JsonResponse`, `RetryPolicy`, and `HttpClient`**

Implement these signatures in `src/fetchers/http.py`:

```python
@dataclass(frozen=True, slots=True)
class RetryPolicy:
    attempts: int = 3
    connect_timeout: float = 10.0
    read_timeout: float = 30.0
    backoff_base: float = 2.0
    backoff_cap: float = 60.0
    per_host_pacing: float = 0.25


@dataclass(frozen=True, slots=True)
class JsonResponse:
    data: dict | list | None
    status_code: int
    etag: str | None
    not_modified: bool


@dataclass(frozen=True, slots=True)
class TextResponse:
    text: str
    status_code: int
    etag: str | None
    not_modified: bool


class HttpClient:
    def __init__(
        self,
        session: requests.Session | None = None,
        retry: RetryPolicy = RetryPolicy(),
        pacer: HostPacer = GLOBAL_HOST_PACER,
        sleep: Callable[[float], None] = time.sleep,
        jitter: Callable[[], float] = random.random,
    ) -> None:
        self.session = session or requests.Session()
        self.retry = retry
        self.pacer = pacer
        self.host_interval = retry.per_host_pacing
        self.sleep = sleep
        self.jitter = jitter

    def get_json(
        self,
        url: str,
        *,
        params: dict | None = None,
        headers: dict | None = None,
        etag: str | None = None,
    ) -> JsonResponse:
        return self._request_json("GET", url, params=params, headers=headers, etag=etag)

    def get_text(
        self,
        url: str,
        *,
        params: dict | None = None,
        headers: dict | None = None,
        etag: str | None = None,
    ) -> TextResponse:
        return self._request_text("GET", url, params=params, headers=headers, etag=etag)

    def post_json(
        self,
        url: str,
        *,
        payload: dict,
        headers: dict | None = None,
    ) -> JsonResponse:
        return self._request_json("POST", url, payload=payload, headers=headers)
```

`HostPacer` owns one lock and a hostname-to-next-allowed-time mapping shared by every default client. `_request_json()` and `_request_text()` call it before each attempt, use policy-supplied connect/read timeouts, add `If-None-Match` when an ETag exists, and return without decoding on 304. Retry only timeouts, connection errors, 429, 500, 502, 503, and 504. Honor both integer-seconds and HTTP-date `Retry-After`; otherwise sleep the configured exponential backoff plus jitter, capped by policy. Raise after the configured total attempts. Build errors from method, hostname, and status only, never the full token-bearing path or response body. `get_text()` exists only for verified official HTML detail pages such as Amazon and applies the identical retry, pacing, timeout, and redaction rules.

- [ ] **Step 4: Replace the base fetcher API**

In `src/fetchers/base.py`, inject `HttpClient`, change the abstract signature, and add default enrichment:

```python
class Fetcher(ABC):
    name: ClassVar[str] = ""

    def __init__(self, http: HttpClient | None = None) -> None:
        self.http = http or HttpClient()

    @abstractmethod
    def fetch(self, company: dict, context: FetchContext) -> FetchResult:
        raise NotImplementedError

    def fetch_detail(self, company: dict, job: Job) -> DetailResult:
        return DetailResult(
            job=None,
            status=DetailStatus.FAILED,
            fetched_at=datetime.now(timezone.utc).isoformat(),
            error="detail verification is not implemented for this adapter",
        )


def source_key(company: dict) -> str:
    if company["fetcher"] == "workday":
        identity = ":".join((company["host"], company["tenant"], company["site"]))
    else:
        identity = company.get("slug") or company.get("board") or company["name"]
    return f"{company['fetcher']}:{identity}"


def get_fetcher(name: str, http: HttpClient | None = None) -> Fetcher:
    if name not in _REGISTRY:
        raise KeyError(f"No fetcher named '{name}'. Available: {sorted(_REGISTRY)}")
    return _REGISTRY[name](http=http)
```

Create `tests/fakes.py` for adapter tests:

```python
@dataclass(frozen=True, slots=True)
class HttpCall:
    method: str
    url: str
    params: dict | None
    payload: dict | None
    etag: str | None


class FakeHttp:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def queue_json(self, data, status_code=200, etag=None, not_modified=False):
        self.responses.append(JsonResponse(data, status_code, etag, not_modified))

    def queue_error(self, error):
        self.responses.append(error)

    def queue_text(self, value, status_code=200, etag=None, not_modified=False):
        self.responses.append(TextResponse(value, status_code, etag, not_modified))

    def _next(self):
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        if isinstance(item, (JsonResponse, TextResponse)):
            return item
        return JsonResponse(item, 200, None, False)

    def get_json(self, url, *, params=None, headers=None, etag=None):
        self.calls.append(HttpCall("GET", url, params, None, etag))
        return self._next()

    def get_text(self, url, *, params=None, headers=None, etag=None):
        self.calls.append(HttpCall("GET_TEXT", url, params, None, etag))
        return self._next()

    def post_json(self, url, *, payload, headers=None):
        self.calls.append(HttpCall("POST", url, None, payload, None))
        return self._next()


def load_json_fixture(name: str):
    fixture_path = Path(__file__).parent / "fixtures" / name
    return json.loads(fixture_path.read_text(encoding="utf-8"))
```

Import `json` and `Path` in `tests/fakes.py`. Adapter test modules may define a local `fake_http` pytest fixture from `FakeHttp` plus the exact ordered fixture names stated in that adapter task; alternate error and pagination paths construct their own `FakeHttp` instances so no test depends on live network or hidden shared state.

- [ ] **Step 5: Run the focused tests**

```bash
python -m pytest tests/test_http_client.py tests/test_fetcher_base.py -v
```

Expected: all HTTP and base-contract tests pass.

- [ ] **Step 6: Commit the transport contract**

```bash
git add src/fetchers/http.py src/fetchers/base.py tests/fakes.py tests/test_http_client.py tests/test_fetcher_base.py
git commit -m "feat: add resilient fetch contract"
```

---

### Task 3: Migrate and atomically persist state version 2

**Files:**
- Modify: `src/state.py:1-64`
- Create: `src/dedupe.py`
- Create: `tests/fixtures/state_v1.json`
- Create: `tests/test_state_migration.py`
- Create: `tests/test_state_persistence.py`
- Create: `tests/test_dedupe_migration.py`

**Interfaces:**
- Consumes: model keys and revision hashes from Task 1
- Produces: canonical identity helpers, `StateCorruptionError`, `StateManager.load()`, `StateManager.fetch_context()`, queue/read/receipt APIs for immediate, moderate, and health-summary delivery, `StateManager.candidate_id_for_source_ref()`, `StateManager.invalidate_candidate_queue()`, `StateManager.queue_item_is_current()`, `StateManager.record_run_eligibility()`, `StateManager.complete_digest()`, `StateManager.complete_health_summary()`, `StateManager.persisted_fingerprint()`, `StateManager.prune()`, and `StateManager.save_atomic()`

- [ ] **Step 1: Add a representative version-1 fixture and failing migration tests**

Create `tests/fixtures/state_v1.json`:

```json
{
  "seen_jobs": {
    "Figure:123": {
      "first_seen": "2026-05-28T04:05:57+00:00",
      "title": "Robotics Test Engineer",
      "location": "Sunnyvale, CA",
      "url": "https://example.test/jobs/123",
      "alerted": true
    },
    "Figure:999": {
      "first_seen": "2026-05-28T04:06:00+00:00",
      "title": "Senior Counsel",
      "location": "Sunnyvale, CA",
      "url": "https://example.test/jobs/999",
      "alerted": false
    }
  },
  "company_failures": {
    "Figure": {"count": 6, "last_error": "old failure"}
  }
}
```

In `tests/test_state_migration.py`, assert schema version 2, preservation of the alerted identity in the delivery ledger, removal of the old non-alerted bulk record, and that legacy six-failure counters cannot leave a source permanently skipped. Version-2 health begins from the new source key and may fetch immediately. Assert that loading a valid version-1 file marks the manager dirty and unpersisted, and that the first `save_atomic()` writes schema version 2. Parameterize schemaless `{}`, a random object, wrong `seen_jobs` type, wrong `company_failures` type, and unknown top-level keys; each raises `StateCorruptionError` instead of being treated as version 1. In `tests/test_dedupe_migration.py`, add `test_migrated_alerted_job_does_not_requeue_by_url_or_posting_id`: migrate the alerted fixture, observe its equivalent normalized official URL and posting ID, and prove the first matching assessment becomes a suppressed migration baseline while a later title or location change is eligible. Assert the migrated record preserves normalized company, title, location, URL, and complete compact defaults so equivalence is testable. In `tests/test_state_persistence.py`, write malformed JSON and assert `StateCorruptionError`; monkeypatch `os.replace` and assert it is used for a successful save.

- [ ] **Step 2: Run the state tests and verify they fail**

```bash
python -m pytest tests/test_state_migration.py tests/test_state_persistence.py tests/test_dedupe_migration.py -v
```

Expected: tests fail because versioned migration, hard corruption failure, and atomic save do not exist.

- [ ] **Step 3: Implement the version-2 schema and migration**

Use this exact top-level schema in `src/state.py`:

```python
def empty_state() -> dict:
    return {
        "schema_version": 2,
        "sources": {},
        "candidates": {},
        "delivery": {"pending_immediate": {}, "delivered": {}},
        "digest": {
            "pending_moderate": {},
            "pending_health_summaries": {},
            "delivered_health_summaries": {},
            "last_processed_date": None,
            "last_processed_at": None,
            "last_health_summary_date": None,
            "last_health_summary_at": None,
        },
        "runs": {},
        "meta": {
            "seeded_at": None,
            "last_health_checkpoint": None,
            "record_updated_at": None,
        },
    }
```

Implement migration with these rules:

```python
def migrate_v1(raw: dict) -> dict:
    migrated = empty_state()
    for old_key, item in raw.get("seen_jobs", {}).items():
        if not item.get("alerted"):
            continue
        company, _, posting_id = old_key.partition(":")
        candidate_id = canonical_candidate_id(company, posting_id, item.get("url", ""))
        legacy_revision = f"{candidate_id}:0:legacy"
        migrated["candidates"][candidate_id] = {
            "first_seen_at": item.get("first_seen"),
            "last_seen_at": item.get("first_seen"),
            "last_verified_open_at": None,
            "closed_at": None,
            "last_closed_at": None,
            "reopened_at": None,
            "aliases": candidate_aliases_from_legacy(company, posting_id, item.get("url", "")),
            "source_refs": {},
            "discovered_during_seed": False,
            "migration_baseline_pending": True,
            "migration_snapshot": {
                "company": normalize_text(company),
                "title": normalize_text(item.get("title", "")),
                "location": normalize_text(item.get("location", "")),
                "url": normalize_official_url(item.get("url", "")),
            },
            "reopen_generation": 0,
            "last_content_hash": None,
            "last_evaluation": None,
            "seed_baseline": None,
            "last_alert_basis": None,
            "last_queued_revision_id": None,
            "last_queue_invalidation": None,
            "record_updated_at": item.get("first_seen"),
        }
        migrated["delivery"]["delivered"][legacy_revision] = {
            "delivered_at": item.get("first_seen"),
            "candidate_id": candidate_id,
            "reopen_generation": 0,
            "chunk_id": "legacy-v1",
            "message_id": None,
            "queued_run_id": "legacy-v1",
            "fetch_completed_at": item.get("first_seen"),
            "identity_aliases": durable_identity_aliases_from_legacy(
                company, posting_id, item.get("url", "")
            ),
            "source_key": None,
            "record_updated_at": item.get("first_seen"),
        }
    # Version-1 company failure counters used names rather than stable source keys.
    # Do not migrate them into an unprobeable circuit; new adapters establish health.
    return migrated
```

Implement URL normalization, source-scoped aliases, trusted employer-requisition aliases, and the legacy alias helpers in `src/dedupe.py` before writing migration. Migration stores both normalized URL and `legacy-local:<normalized-company>:<old-posting-id>`. Resolution may consult that local-ID alias only for a record with `migration_baseline_pending=True`; after attaching the real source alias and suppressing its first equivalent assessment, it removes the legacy-local alias. Outside that one-time migration bridge, local ATS IDs are never treated as cross-source employer requisitions. `Job.requisition_id` is optional and may form a cross-source alias only when its provenance is `FactSource.OFFICIAL_DETAIL` or `FactSource.STRUCTURED_FEED`. URL aliases merge only into an active candidate; after closure, a new source posting becomes a new candidate. `validate_v1()` accepts only the exact legacy top-level keys `seen_jobs` and optional `company_failures`, requires both values to be objects, and validates every legacy item field before migration. Arbitrary schemaless JSON is corruption, not version 1. `StateManager.load(path)` must distinguish missing files from malformed files. Missing files receive `empty_state()`. Malformed or structurally invalid files raise `StateCorruptionError` and do not overwrite the original.

- [ ] **Step 4: Implement transactional delivery and atomic persistence methods**

Add these public signatures:

```python
@dataclass(frozen=True, slots=True)
class StateLimits:
    closed_candidate_days: int = 90
    delivered_days: int = 365
    delivered_limit: int = 10_000
    pending_max_age_days: int = 30
    digest_entry_days: int = 30
    health_event_limit: int = 30
    run_ledger_days: int = 14


class StateManager:
    @classmethod
    def load(cls, path: str | Path, limits: StateLimits = StateLimits()) -> "StateManager":
        state_path = Path(path)
        if not state_path.exists():
            manager = cls(state_path, empty_state(), limits)
            manager.dirty = True
            manager._persisted_fingerprint = "missing"
            return manager
        try:
            raw = json.loads(state_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise StateCorruptionError(f"invalid JSON in {state_path.name}") from exc
        migrated = False
        if "schema_version" not in raw:
            validate_v1(raw)
            raw = migrate_v1(raw)
            migrated = True
        if raw.get("schema_version") != 2:
            raise StateCorruptionError("unsupported state schema")
        validate_v2(raw)
        manager = cls(state_path, raw, limits)
        manager._persisted_fingerprint = canonical_state_hash(raw)
        manager.dirty = migrated
        return manager

    def fetch_context(self, source: str, now: datetime) -> FetchContext:
        record = self.state["sources"].get(source, {})
        due_ids = frozenset(
            posting_id
            for posting_id, due_at in record.get("detail_retry_ids", {}).items()
            if parse_utc(due_at) <= now
        )
        return FetchContext(
            previous_etag=record.get("etag"),
            previous_fingerprint=record.get("fingerprint"),
            previous_active_ids=frozenset(record.get("active_ids", [])),
            detail_retry_ids=due_ids,
            force_full=bool(due_ids),
        )

    def queue_immediate(self, item: AlertItem) -> bool:
        return self._queue(item, destination="pending_immediate")

    def queue_moderate(self, item: AlertItem) -> bool:
        return self._queue(item, destination="pending_moderate")

    def _queue(self, item: AlertItem, destination: str) -> bool:
        delivery = self.state["delivery"]
        if item.revision_id in delivery["delivered"]:
            return False
        if item.revision_id in delivery["pending_immediate"]:
            return False
        if item.revision_id in self.state["digest"]["pending_moderate"]:
            if destination == "pending_moderate":
                return False
            existing = self.state["digest"]["pending_moderate"].pop(item.revision_id)
            assert_same_alert_identity(existing, alert_item_to_dict(item))
            remove_undelivered_revisions_for_candidate(
                self.state,
                candidate_id=item.candidate_id,
                except_revision_id=item.revision_id,
            )
            delivery["pending_immediate"][item.revision_id] = existing
            self.dirty = True
            return True
        remove_undelivered_revisions_for_candidate(
            self.state,
            candidate_id=item.candidate_id,
            except_revision_id=item.revision_id,
        )
        target = (
            self.state["digest"]["pending_moderate"]
            if destination == "pending_moderate"
            else delivery["pending_immediate"]
        )
        target[item.revision_id] = alert_item_to_dict(item)
        self.dirty = True
        return True

    def pending_immediate(self) -> tuple[AlertItem, ...]:
        values = self.state["delivery"]["pending_immediate"].values()
        ordered = sorted(values, key=lambda item: (item["queued_at"], item["revision_id"]))
        return tuple(alert_item_from_dict(item) for item in ordered)

    def pending_moderate(self) -> tuple[AlertItem, ...]:
        values = self.state["digest"]["pending_moderate"].values()
        ordered = sorted(values, key=lambda item: (item["queued_at"], item["revision_id"]))
        return tuple(alert_item_from_dict(item) for item in ordered)

    def candidate_id_for_source_ref(
        self,
        source_key: str,
        posting_id: str,
    ) -> str | None:
        ref_key = f"{source_key}|{posting_id}"
        matches = sorted(
            candidate_id
            for candidate_id, record in self.state["candidates"].items()
            if ref_key in record.get("source_refs", {})
        )
        if len(matches) > 1:
            raise StateCorruptionError("source reference belongs to multiple candidates")
        return matches[0] if matches else None

    def invalidate_candidate_queue(
        self,
        candidate_id: str,
        reason: QueueInvalidationReason,
        invalidated_at: datetime,
        *,
        source_key: str | None = None,
    ) -> tuple[str, ...]:
        require_aware_utc(invalidated_at)
        record = self.state["candidates"][candidate_id]
        queues = (
            self.state["delivery"]["pending_immediate"],
            self.state["digest"]["pending_moderate"],
        )
        revision_ids = tuple(sorted({
            revision_id
            for queue in queues
            for revision_id, item in queue.items()
            if item["candidate_id"] == candidate_id
            and (source_key is None or item["source_key"] == source_key)
        }))
        if not revision_ids:
            return ()
        for queue in queues:
            for revision_id in revision_ids:
                queue.pop(revision_id, None)
        invalidated_iso = invalidated_at.isoformat().replace("+00:00", "Z")
        if record.get("last_queued_revision_id") in revision_ids:
            record["last_queued_revision_id"] = None
        prior_invalidated = set(
            (record.get("last_queue_invalidation") or {}).get("revision_ids", ())
        )
        record["last_queue_invalidation"] = {
            "revision_ids": sorted(prior_invalidated.union(revision_ids)),
            "reason": reason.value,
            "invalidated_at": invalidated_iso,
        }
        record["record_updated_at"] = invalidated_iso
        self.dirty = True
        return revision_ids

    def queue_item_is_current(self, item: AlertItem) -> bool:
        record = self.state["candidates"].get(item.candidate_id)
        if record is None:
            return False
        evaluation = record.get("last_evaluation") or {}
        invalidated_ids = set(
            (record.get("last_queue_invalidation") or {}).get("revision_ids", ())
        )
        return (
            record.get("closed_at") is None
            and evaluation.get("eligible") is True
            and evaluation.get("recommendation") != Recommendation.SKIP.value
            and record.get("last_queued_revision_id") == item.revision_id
            and item.revision_id not in invalidated_ids
        )

    def queue_health_summary(self, item: PendingHealthSummary) -> bool:
        pending = self.state["digest"]["pending_health_summaries"]
        delivered = self.state["digest"]["delivered_health_summaries"]
        if item.delivery_id in pending or item.delivery_id in delivered:
            return False
        pending[item.delivery_id] = health_summary_to_dict(item)
        self.dirty = True
        return True

    def pending_health_summaries(self) -> tuple[PendingHealthSummary, ...]:
        values = self.state["digest"]["pending_health_summaries"].values()
        return tuple(health_summary_from_dict(item) for item in sorted(
            values, key=lambda item: (item["queued_at"], item["delivery_id"])
        ))

    def mark_health_summary_delivered(
        self, delivery_id: str, delivered_at: str, message_id: int
    ) -> None:
        item = self.state["digest"]["pending_health_summaries"].pop(delivery_id)
        self.state["digest"]["delivered_health_summaries"][delivery_id] = {
            "local_date": item["local_date"],
            "delivered_at": delivered_at,
            "message_id": message_id,
            "record_updated_at": delivered_at,
        }
        self.dirty = True

    def record_run_eligibility(
        self,
        run_id: str,
        revision_ids: Sequence[str],
        fetch_completed_at: str,
        created_at: str,
    ) -> None:
        changed = upsert_run_eligibility(
            self.state["runs"], run_id, revision_ids, fetch_completed_at, created_at
        )
        self.dirty = self.dirty or changed

    def mark_chunk_delivered(
        self,
        delivery_ids: Sequence[str],
        delivered_at: str,
        chunk_id: str,
        message_id: int,
    ) -> None:
        for revision_id in delivery_ids:
            immediate = self.state["delivery"]["pending_immediate"].get(revision_id)
            moderate = self.state["digest"]["pending_moderate"].get(revision_id)
            if immediate is not None and moderate is not None:
                assert_same_alert_identity(immediate, moderate)
            item = immediate or moderate
            if item is not None:
                self.state["delivery"]["pending_immediate"].pop(revision_id, None)
                self.state["digest"]["pending_moderate"].pop(revision_id, None)
                self.state["delivery"]["delivered"][revision_id] = {
                    "delivered_at": delivered_at,
                    "candidate_id": item["candidate_id"],
                    "reopen_generation": item["reopen_generation"],
                    "chunk_id": chunk_id,
                    "message_id": message_id,
                    "queued_run_id": item["queued_run_id"],
                    "fetch_completed_at": item["fetch_completed_at"],
                    "identity_aliases": item["identity_aliases"],
                    "source_key": item["source_key"],
                    "record_updated_at": delivered_at,
                }
                self.dirty = True

    def complete_digest(self, local_date: str, completed_at: str) -> None:
        changed = set_completion_if_newer(
            self.state["digest"],
            date_key="last_processed_date",
            timestamp_key="last_processed_at",
            local_date=local_date,
            completed_at=completed_at,
        )
        self.dirty = self.dirty or changed

    def complete_health_summary(self, local_date: str, completed_at: str) -> None:
        changed = set_completion_if_newer(
            self.state["digest"],
            date_key="last_health_summary_date",
            timestamp_key="last_health_summary_at",
            local_date=local_date,
            completed_at=completed_at,
        )
        self.dirty = self.dirty or changed

    def prune(self, now: datetime) -> None:
        self.dirty = prune_state(self.state, now, self.limits) or self.dirty

    def persisted_fingerprint(self) -> str:
        return self._persisted_fingerprint

    def is_persisted(self) -> bool:
        return not self.dirty and canonical_state_hash(self.state) == self._persisted_fingerprint

    def save_atomic(self) -> None:
        if not self.dirty:
            return
        atomic_write_json(self.path, self.state)
        self._persisted_fingerprint = canonical_state_hash(self.state)
        self.dirty = False
```

Import `Sequence` from `collections.abc`. Implement `load()` above these methods to validate the schema and construct `StateManager(path, state, limits)`. A missing or migrated file is dirty and unpersisted until `save_atomic()` succeeds; a validated version-2 file caches its canonical hash as persisted. After every successful save, cache the canonical state hash in `_persisted_fingerprint`; mutations do not update it. Add `is_persisted()` that requires `dirty is False` and the current canonical hash equals that baseline. Implement symmetric serializers for alerts and health summaries without a description field. Each serialized pending queue record includes `record_updated_at`, set exactly to its immutable `queued_at`; the deserializer checks equality and does not expose a second mutable clock on `AlertItem` or `PendingHealthSummary`. Delta conflict resolution therefore always has a defined queue timestamp. Store `reopen_generation`, `source_key`, and the candidate's compact `identity_aliases` on `AlertItem` and its receipt so a later pruned candidate cannot reuse a delivered generation and delivery can attribute failures safely. Before inserting a new material revision, `_queue()` atomically removes older undelivered immediate or moderate revisions for the same candidate; delivered revisions remain immutable. A duplicate in its current destination is a no-op. Promoting the same revision from moderate to immediate moves the already serialized record, validates its immutable identity against the caller, and preserves its original `queued_at`, `queued_run_id`, `fetch_completed_at`, and `record_updated_at`. `candidate_id_for_source_ref()` is read-only and fails on corrupt duplicate ownership. `invalidate_candidate_queue()` removes pending revisions for one candidate, optionally restricted to items whose immutable `source_key` matches the supplied source, records the exact removed revision IDs and reason, clears `last_queued_revision_id` only when it named a removed revision, and is a no-op when nothing is pending. Candidate-wide closure, ineligibility, and delivery-stale checks omit the optional filter; a single source-reference closure or rejection supplies it so another verified source's queue can survive. A later eligible assessment whose revision ID appears in that marker may be queued again because it was never delivered; `record_alert_basis()` then clears the marker after the successful queue insertion. Delivered revisions remain suppressed. `queue_item_is_current()` is the defensive pre-send gate. Add tests proving Moderate-to-Strong promotion cannot leave a stale digest entry, same-revision destination promotion preserves all queue clocks, pending serializers round-trip `record_updated_at`, filtered and candidate-wide invalidation are idempotent, an invalidated undelivered revision can be queued again after recovery, and `mark_chunk_delivered()` validates any defensive duplicate then removes the revision from both queues. Health-summary intent also must be persisted before transport. The compact per-run ledger retains the union of first-eligible immediate revision IDs even if one is later superseded, so metrics can reconstruct the denominator. Implement `prune_state()` to return whether semantic state changed, expire still-valid pending alerts after `pending_max_age_days`, retain run ledgers for 14 days, and set `dirty` only when true. Implement `atomic_write_json()` to write compact, sorted JSON to a temporary file in the destination directory, flush and `fsync()` it, call `os.replace(temp_name, path)`, and remove the temporary file on pre-replacement failure.

Candidate records are compact dictionaries with `first_seen_at`, `last_seen_at`, `last_verified_open_at`, `closed_at`, `last_closed_at`, `reopened_at`, `reopen_generation`, `discovered_during_seed`, `migration_baseline_pending`, optional `migration_snapshot`, `aliases`, `source_refs`, `last_content_hash`, `last_evaluation`, immutable `seed_baseline`, `last_alert_basis`, `last_queued_revision_id`, optional `last_queue_invalidation`, and `record_updated_at`. A source reference contains `source_key`, `posting_id`, `missing_count`, `closed_at`, `last_detail_hash`, and `record_updated_at`. Source records contain `seeded_at`, canonical `detail_retry_ids: {posting_id: due_at}`, `last_complete_at`, and `record_updated_at`. Assessment snapshots contain `eligible`, recommendation, title, employment type, required-experience bounds and unresolved status, authorization status, location, salary bounds/currency, score, and timestamp, but never the description. Each run-ledger record contains exact keys `eligible_immediate_revision_ids`, `started_at`, `fetch_completed_at`, and `record_updated_at`; it keeps sorted unique revision IDs, the earliest `started_at`, the latest `fetch_completed_at`, and sets `record_updated_at` to the latest semantic update time supplied as `created_at`. This timestamp is the sole conflict clock for run upserts and retention tombstones. Digest completion is stored as the paired fields `last_processed_date` plus `last_processed_at`; health completion uses `last_health_summary_date` plus `last_health_summary_at`. `set_completion_if_newer()` validates a `YYYY-MM-DD` local date and aware UTC ISO timestamp, compares date first and timestamp second, updates both fields atomically only when the pair advances, and is otherwise a byte-for-byte no-op. Live delivered receipts require `source_key`; migrated legacy receipts use `source_key: null` because version 1 did not retain a stable source identity. Task 4 adds candidate mutation methods after these identity helpers exist.

- [ ] **Step 5: Run state tests and the original migration against a temporary destination**

```bash
python -m pytest tests/test_state_migration.py tests/test_state_persistence.py tests/test_dedupe_migration.py -v
python -c 'from src.state import StateManager; s=StateManager.load("state.json"); print(s.state["schema_version"], len(s.state["delivery"]["delivered"]))'
```

Expected: tests pass; the read-only migration preview prints schema `2` and a nonzero legacy delivered count without modifying `state.json`.

- [ ] **Step 6: Commit the state migration**

```bash
git add src/state.py src/dedupe.py tests/fixtures/state_v1.json tests/test_state_migration.py tests/test_state_persistence.py tests/test_dedupe_migration.py
git commit -m "feat: add transactional state v2"
```

---

### Task 4: Add source lifecycle, circuit recovery, and canonical deduplication

**Files:**
- Modify: `src/state.py`
- Create: `src/source_health.py`
- Create: `src/lifecycle.py`
- Modify: `src/dedupe.py`
- Create: `tests/test_source_lifecycle.py`
- Create: `tests/test_dedupe.py`

**Interfaces:**
- Consumes: `FetchContext`, `FetchResult`, `CircuitState`, and version-2 state
- Produces: `SourceHealthPolicy`, `RevisionPolicy`, `should_probe()`, `classify_snapshot()`, `reconcile_inventory()`, `comparison_basis()`, `is_migration_equivalent()`, `should_alert_revision()`, `material_detail_hash()`, `max_delivered_generation()`, `StateManager.should_fetch()`, `StateManager.begin_fetch()`, `StateManager.apply_fetch_result()`, `StateManager.mark_source_seeded()`, `StateManager.apply_detail_result()`, `StateManager.cancel_detail_retry()`, `StateManager.observe_candidate()`, `StateManager.record_assessment()`, `StateManager.record_migration_baseline()`, `StateManager.record_alert_basis()`, `StateManager.source()`, `StateManager.candidate()`, `SourceTransition`, `candidate_aliases()`, `durable_identity_aliases()`, `resolve_candidate_id()`, `canonical_job_key()`, and `probable_duplicate_key()`

- [ ] **Step 1: Write failing lifecycle and closure tests**

Cover the exact safety rules in `tests/test_source_lifecycle.py`:

```python
NOW = datetime(2026, 9, 7, 12, tzinfo=timezone.utc)
LATER = NOW + timedelta(minutes=30)
MUCH_LATER = LATER + timedelta(minutes=30)


def fetch_result(*, complete, health, active_ids, source_total=None, unchanged=False, at=NOW):
    ids = frozenset(active_ids)
    jobs = tuple(
        Job(
            source_type="greenhouse",
            source_key="greenhouse:figureai",
            company="Figure",
            job_id=posting_id,
            title="Robotics Test Engineer",
            location="Sunnyvale, CA",
            url=f"https://example.test/jobs/{posting_id}",
            employment_type=EmploymentType.FULL_TIME,
            country_code="US",
        )
        for posting_id in sorted(ids)
    ) if complete and not unchanged else ()
    return FetchResult(
        jobs=jobs,
        active_ids=ids,
        complete=complete,
        source_total=source_total,
        total_is_authoritative=source_total is not None,
        pages_fetched=1,
        fetched_at=at.isoformat(),
        health=FetchHealth(health),
        unchanged=unchanged,
    )


def failed_result(at):
    return fetch_result(complete=False, health="failed", active_ids=set(), at=at)


def healthy_result(at):
    job = Job(
        source_type="tesla",
        source_key="tesla:careers",
        company="Tesla",
        job_id="123",
        title="Robotics Test Engineer",
        location="Sunnyvale, CA",
        url="https://example.test/jobs/123",
        employment_type=EmploymentType.FULL_TIME,
        country_code="US",
    )
    return FetchResult(
        jobs=(job,),
        active_ids=frozenset({"123"}),
        complete=True,
        source_total=1,
        total_is_authoritative=True,
        pages_fetched=1,
        fetched_at=at.isoformat(),
        health=FetchHealth.HEALTHY,
    )


@pytest.fixture
def state_with_active_job(tmp_path):
    state = StateManager.load(tmp_path / "state.json")
    state.state["sources"]["greenhouse:figureai"] = {
        "active_ids": ["123"],
        "missing_counts": {},
        "health": "healthy",
        "circuit": "closed",
        "consecutive_failures": 0,
    }
    state.state["candidates"]["posting:figure:123"] = {
        "first_seen_at": NOW.isoformat(),
        "source_refs": {
            "greenhouse:figureai|123": {
                "source_key": "greenhouse:figureai",
                "posting_id": "123",
                "missing_count": 0,
                "closed_at": None,
            }
        },
        "closed_at": None,
        "last_seen_at": NOW.isoformat(),
    }
    return state


@pytest.fixture
def empty_state(tmp_path):
    return StateManager.load(tmp_path / "empty-state.json")


def test_partial_and_403_results_never_close_jobs(state_with_active_job):
    partial = fetch_result(complete=False, health="partial", active_ids=set())
    state_with_active_job.apply_fetch_result("greenhouse:figureai", partial, NOW)
    assert state_with_active_job.candidate("posting:figure:123")["closed_at"] is None


def test_two_complete_omissions_close_a_job(state_with_active_job):
    empty = fetch_result(complete=True, health="empty-valid", active_ids=set(), source_total=0)
    state_with_active_job.apply_fetch_result("greenhouse:figureai", empty, NOW)
    assert state_with_active_job.candidate("posting:figure:123")["closed_at"] is None
    state_with_active_job.apply_fetch_result("greenhouse:figureai", empty, LATER)
    assert state_with_active_job.candidate("posting:figure:123")["closed_at"] == LATER.isoformat()


def test_three_failures_open_circuit_and_successful_daily_probe_closes_it(empty_state):
    for moment in (NOW, LATER, MUCH_LATER):
        empty_state.apply_fetch_result("tesla:careers", failed_result(moment), moment)
    assert empty_state.should_fetch("tesla:careers", MUCH_LATER) is False
    probe_time = MUCH_LATER + timedelta(hours=24)
    assert empty_state.should_fetch("tesla:careers", probe_time) is True
    assert empty_state.begin_fetch("tesla:careers", probe_time) is True
    assert empty_state.source("tesla:careers")["circuit"] == "half-open"
    empty_state.apply_fetch_result("tesla:careers", healthy_result(probe_time), probe_time)
    assert empty_state.source("tesla:careers")["circuit"] == "closed"


def test_304_reuses_previous_active_ids(state_with_active_job):
    unchanged = fetch_result(
        complete=True,
        health="healthy",
        active_ids={"123"},
        unchanged=True,
    )
    state_with_active_job.apply_fetch_result("greenhouse:figureai", unchanged, NOW)
    assert state_with_active_job.source("greenhouse:figureai")["active_ids"] == ["123"]
```

Add a shrink test where 100 preceding IDs collapse to 39 with `total_is_authoritative=False` and assert health becomes partial. Add an override case where `source_total=39`, `total_is_authoritative=True`, and all 39 IDs are present, which remains complete. A merely non-null inferred total never overrides shrink protection.

Add revision tests proving an unchanged content hash does not alert again; a higher recommendation tier does; title, full-time status, required experience, authorization, or location changes do; and compensation re-alerts only when a bound changes at least 10 percent or crosses the $100,000 threshold.

Add candidate-resolution tests proving two active official source records with the same company and trusted employer requisition ID merge; local ATS job IDs remain source-scoped and do not auto-merge; active records with different source IDs but the same normalized official application URL merge; a new posting ID arriving after the prior URL candidate closed becomes a new repost candidate; and a same-ID reopen alerts only after seven closed days or a material change. A closed candidate returning under a newly attached durable source or trusted requisition alias increments `reopen_generation` immediately even before seven days, so unchanged material facts cannot collide with an earlier delivered revision. Add a seed-baseline test proving an unchanged job observed during seed is not queued on the next live run, while a later material revision is eligible for re-evaluation. Record two non-alerting semantic assessments after seed and prove `comparison_basis()` still returns the immutable original `seed_baseline`, not the latest evaluation. Add a retention regression: after a closed candidate is pruned at 90 days, its retained delivery records preserve `identity_aliases` and the maximum `reopen_generation`; if a new `Job` carries an intersecting source-scoped or trusted requisition alias, `observe_candidate()` scans those retained receipts and starts the new record at that maximum plus one so its revision ID cannot collide with a delivered revision. When no retained receipt intersects, `max_delivered_generation()` returns `-1`, so a genuinely new candidate starts at generation zero. A URL-only alias from a closed/pruned candidate is not enough to merge a repost.

Add detail-retry tests. The source record is the single canonical owner: `detail_retry_ids` is a mapping from posting ID to an ISO `due_at`, and source references do not duplicate that value. A failed detail response stores the earlier of an existing due time and `now`, which makes it due on the next source scan without deferring an already scheduled retry; it preserves the candidate's open state and leaves it ineligible. On the next due source scan, `fetch_context()` exposes only due IDs and sets `force_full=True`; an ETag-capable adapter must omit `If-None-Match`, obtain a full listing, and retry detail. A healthy or closed detail result clears the retry ID. Reconciliation also clears it when two accepted omissions close the source reference, and the orchestrator calls `cancel_detail_retry(source_key, posting_id, now)` when a fresh list record no longer passes Stage 1. A 304 may be reused only when there is no due unresolved detail retry. Save/reload tests cover exact due-time scheduling, future-not-due behavior, each clearing route, and prove a stale retry cannot force full fetches forever. Add negative tests proving `apply_fetch_result()` rejects a Job from another `source_key` and `apply_detail_result()` rejects a healthy detail Job whose source key or posting ID differs from its method arguments, with byte-for-byte unchanged state after either rejection.

Add per-source seed tests. `mark_source_seeded(source_key, seeded_at)` succeeds only after that source has an accepted complete snapshot, is monotonic, and never clears an existing seed marker. A live candidate from a source with no `seeded_at` is observed as a baseline but cannot alert; once the source is seeded, the unchanged candidate remains suppressed and a later material revision may alert.

Add queue-validity tests with a persisted immediate item and a persisted Moderate item. An official detail close and a second accepted complete omission remove queue entries owned by that source before any delivery. In a cross-source candidate, closing source A invalidates its queued item even while source B keeps the candidate open, while an item already projected from source B survives the source-A filter. Candidate-wide closure removes all remaining items. A current list row that fails Stage 1 can be resolved through `candidate_id_for_source_ref()` and invalidated with `STAGE_ONE_REJECTED` plus its source filter; an ineligible or Skip assessment uses candidate-wide `ASSESSMENT_INELIGIBLE`. Assert the marker stores exact revision IDs, reason, and UTC time; `queue_item_is_current()` rejects closed, ineligible, superseded, missing-candidate, and invalidated records. Repeating invalidation without a pending item is inert. When the same undelivered revision later becomes open and eligible, `should_alert_revision()` permits it to queue again and `record_alert_basis()` clears the marker. A delivered revision never receives this exception.

Add a byte-for-byte no-change regression covering an identical healthy inventory, unchanged candidate observation/evaluation, duplicate queue attempt, and a prune that removes nothing. Assert `dirty is False`, serialized state is unchanged, and `save_atomic()` performs no replacement. A routine success timestamp alone is not assigned; the separately configured daily health checkpoint is the bounded way to persist liveness.

- [ ] **Step 2: Write failing cross-source deduplication tests**

In `tests/test_dedupe.py`, assert normalized official URLs deduplicate tracking parameters and trailing slashes, while uncertain title/location matches are only marked probable:

```python
def test_normalized_official_url_is_canonical_across_sources(make_job):
    first = make_job(url="https://company.test/jobs/123/?utm_source=greenhouse")
    second = make_job(source_type="custom", source_key="custom:company", url="https://company.test/jobs/123")
    assert canonical_job_key(first) == canonical_job_key(second)


def test_title_location_fallback_is_probable_not_canonical(make_job):
    first = make_job(job_id="1", title="Robotics Test Engineer", location="Austin, TX")
    second = make_job(job_id="2", title="Robotics  Test Engineer", location="Austin, Texas")
    assert canonical_job_key(first) != canonical_job_key(second)
    assert probable_duplicate_key(first) == probable_duplicate_key(second)
```

- [ ] **Step 3: Run the focused tests and verify they fail**

```bash
python -m pytest tests/test_source_lifecycle.py tests/test_dedupe.py -v
```

Expected: tests fail because lifecycle and deduplication methods are missing.

- [ ] **Step 4: Implement source health and lifecycle transitions**

In `src/source_health.py`, add a frozen `SourceHealthPolicy` with failure threshold 3, probe interval 24 hours, shrink ratio `Decimal("0.40")`, and minimum prior count 20. `classify_snapshot()` applies the shrink rule before any inventory change. `should_probe()` returns true for closed circuits and for open/half-open circuits whose probe time is due. In `src/lifecycle.py`, add a frozen `RevisionPolicy` for the seven-day same-ID reopen rule, 10 percent compensation-change rule, and $100,000 threshold crossing. In this task, extend `StateManager.__init__()` and `StateManager.load()` with keyword-only `health_policy: SourceHealthPolicy = SourceHealthPolicy()` and `revision_policy: RevisionPolicy = RevisionPolicy()`, store both policies, and use them for every health, reopen-generation, and material-revision decision. Later configuration wiring constructs both policies from the loaded values explicitly.

```python
@dataclass(frozen=True, slots=True)
class SourceHealthPolicy:
    failure_threshold: int = 3
    probe_interval: timedelta = timedelta(hours=24)
    shrink_ratio: Decimal = Decimal("0.40")
    shrink_min_previous_count: int = 20


@dataclass(frozen=True, slots=True)
class RevisionPolicy:
    same_id_reopen_days: int = 7
    compensation_material_change_ratio: Decimal = Decimal("0.10")
    compensation_threshold: Decimal = Decimal("100000")
```

Extend the loader to the exact signature `StateManager.load(cls, path: str | Path, limits: StateLimits = StateLimits(), *, health_policy: SourceHealthPolicy = SourceHealthPolicy(), revision_policy: RevisionPolicy = RevisionPolicy()) -> StateManager`, preserving the Task 3 validation and migration body. The implementation passes both policies into `StateManager(...)`; `__init__()` accepts the same two keyword-only policy values and stores them as `self.health_policy` and `self.revision_policy`.

In `src/lifecycle.py`, add a frozen `SourceTransition` dataclass containing `source_key`, `previous_health`, `health`, `previous_circuit`, `circuit`, `closed_job_keys`, and `warnings`. `reconcile_inventory()` increments the matching candidate source reference's `missing_count` only for accepted complete results, closes that reference on omission two, resets it when the posting ID reappears, and marks the candidate closed only when every known source reference is closed. It immediately closes a reference only from `DetailStatus.CLOSED`. Whenever a reference reaches omission two, reconciliation calls `invalidate_candidate_queue(..., COMPLETE_OMISSION_CLOSED, now, source_key=source_key)` before returning, even when another reference keeps the candidate open. If all references close, it additionally guarantees no candidate queue remains. `comparison_basis(record: Mapping[str, Any]) -> Mapping[str, Any] | None` returns `last_alert_basis`, otherwise immutable `seed_baseline`, otherwise `None`; freshness and revision alerting must use this same basis so changes are cumulative from the last alerted or seeded facts. `last_evaluation` is observational history and is never a comparison fallback. `is_migration_equivalent(record: Mapping[str, Any], assessment: JobAssessment) -> bool` compares normalized company, title, location, and official URL against `migration_snapshot` and must return true before `record_migration_baseline()` can run. `should_alert_revision(candidate_record: Mapping[str, Any] | None, assessment: JobAssessment, now: datetime, policy: RevisionPolicy = RevisionPolicy()) -> bool` compares against `comparison_basis()`: it returns true for a qualifying candidate first discovered after seed, a genuinely closed candidate returning under a newly attached durable source or trusted requisition identity, a same-ID reopen after `policy.same_id_reopen_days`, a higher recommendation tier, one of the approved material-field changes, or an eligible revision listed in `last_queue_invalidation.revision_ids` that was invalidated before delivery. Compensation uses `policy.compensation_material_change_ratio` and `policy.compensation_threshold`. Merely attaching another source's posting ID to the same active URL/requisition does not re-alert. It returns false for a seeded unchanged baseline, an already queued or delivered revision, a short same-ID reopen without material change, and text edits that do not change tier or material facts. Add explicit active-cross-source-no-realert and invalidated-undelivered-requeue regressions.

```python
def comparison_basis(record: Mapping[str, Any]) -> Mapping[str, Any] | None:
    return record.get("last_alert_basis") or record.get("seed_baseline")
```

`StateManager.should_fetch()` is read-only. `begin_fetch()` is called on the orchestrator thread before dispatching work; it returns false for a not-yet-due open source and changes a due open source to `half-open` before returning true. Before any mutation, `apply_fetch_result(source_key, result, now)` verifies every `result.jobs` entry has `job.source_key == source_key` and that complete non-304 results contain no duplicate job IDs. It raises `ValueError` on a mismatch and leaves state byte-for-byte unchanged. Have `StateManager.apply_fetch_result()` then call the health and lifecycle modules in this order:

```python
if not result.complete or result.health in {FetchHealth.PARTIAL, FetchHealth.FAILED}:
    failure_fields = {
        "health": result.health.value,
        "consecutive_failures": source.get("consecutive_failures", 0) + 1,
    }
    if failure_fields["consecutive_failures"] >= self.health_policy.failure_threshold:
        failure_fields["circuit"] = CircuitState.OPEN.value
        failure_fields["next_probe_at"] = (
            now + self.health_policy.probe_interval
        ).isoformat()
    changed = set_changed_fields(source, failure_fields)
    changed = append_health_event_on_change(source, result, now) or changed
    if changed:
        source["record_updated_at"] = now.isoformat()
        self.dirty = True
    return transition()

if is_unconfirmed_shrink(source.get("active_ids", []), result):
    failure_fields = {
        "health": FetchHealth.PARTIAL.value,
        "consecutive_failures": source.get("consecutive_failures", 0) + 1,
    }
    if failure_fields["consecutive_failures"] >= self.health_policy.failure_threshold:
        failure_fields["circuit"] = CircuitState.OPEN.value
        failure_fields["next_probe_at"] = (
            now + self.health_policy.probe_interval
        ).isoformat()
    changed = set_changed_fields(source, failure_fields)
    changed = append_health_event_on_change(source, result, now) or changed
    if changed:
        source["record_updated_at"] = now.isoformat()
        self.dirty = True
    return transition()

if result.unchanged:
    effective_active_ids = source.get("active_ids", [])
    effective_etag = result.etag if result.etag is not None else source.get("etag")
    effective_fingerprint = (
        result.fingerprint
        if result.fingerprint is not None
        else source.get("fingerprint")
    )
    effective_source_total = source.get("source_total")
    effective_total_is_authoritative = source.get("total_is_authoritative", False)
else:
    effective_active_ids = sorted(result.active_ids)
    effective_etag = result.etag
    effective_fingerprint = result.fingerprint
    effective_source_total = result.source_total
    effective_total_is_authoritative = result.total_is_authoritative

semantic_fields = {
    "consecutive_failures": 0,
    "circuit": CircuitState.CLOSED.value,
    "next_probe_at": None,
    "health": result.health.value,
    "active_ids": effective_active_ids,
    "etag": effective_etag,
    "fingerprint": effective_fingerprint,
    "source_total": effective_source_total,
    "total_is_authoritative": effective_total_is_authoritative,
}
if set_changed_fields(source, semantic_fields):
    source["last_complete_at"] = result.fetched_at
    source["record_updated_at"] = now.isoformat()
    self.dirty = True
```

Before mutating, capture the previous health/circuit/inventory. On a 304, retain the preceding non-null ETag, fingerprint, authoritative-total marker, source total, and closure counters. Preserve `active_ids`, ETag, fingerprint, total metadata, and closure counters on every incomplete or anomalous result. Persist sanitized warnings and at most 30 transition events. Compare the complete semantic field set first; assign `last_complete_at` and `record_updated_at` only when that set, lifecycle state, or circuit state changes. When health, circuit, inventory, validators, warnings, and candidate state are all unchanged, do not assign a new fetch timestamp or mark dirty. A healthy or empty-valid half-open probe closes the circuit. A failed half-open probe returns to open and schedules the next probe 24 hours later. Every semantic upsert receives `record_updated_at`; `last_complete_at` determines inventory merge precedence later.

Add read-only `source(source_key)` and `candidate(candidate_id)` accessors that raise `KeyError` for missing records; tests and health reporting use them without mutating state.

`material_detail_hash(job: Job) -> str` returns `job.content_hash`. Its canonical payload is therefore exactly requisition ID, title, raw and structured location, description, employment type, workplace type, posted and updated timestamps, salary fields, and sorted per-field provenance; only the SHA-256 digest is persisted. `StateManager.apply_detail_result(candidate_id: str, source_key: str, posting_id: str, result: DetailResult, now: datetime) -> None` validates the candidate and its `source_key|posting_id` reference before any mutation. For a healthy result it additionally requires `result.job.source_key == source_key` and `result.job.job_id == posting_id`; an identity mismatch raises `ValueError`, does not clear a retry, and leaves state byte-for-byte unchanged.

After validation, `apply_detail_result()` closes that source reference immediately only for `DetailStatus.CLOSED` and closes the candidate only when no other active reference remains. Any official detail closure calls `invalidate_candidate_queue(..., OFFICIAL_DETAIL_CLOSED, now, source_key=source_key)`, even if another source reference remains active, because a queued item from the closed source may contain a dead application URL. If no reference remains active, it guarantees candidate-wide invalidation. A later healthy official source may evaluate and queue a current link again. For `DetailStatus.HEALTHY`, compute `material_detail_hash(result.job)` and update `last_verified_open_at`, `last_detail_hash`, and the reference's open state only when reopening a reference, clearing a retry, or observing a different material detail hash. An identical routine verification is byte-for-byte inert. `FAILED` stores `min(existing_due_at, now)` in the source-owned `detail_retry_ids` map, using an aware UTC ISO timestamp. This makes the retry due on the next source scan and never pushes an existing retry later. It leaves candidate and reference open or closed state unchanged. `HEALTHY` and `CLOSED` clear the retry ID. `cancel_detail_retry(source_key, posting_id, now)` is an idempotent semantic mutation used for accepted omissions and list records rejected by Stage 1. `fetch_context()` sets `force_full` only while at least one retry is due.

```python
def material_detail_hash(job: Job) -> str:
    return job.content_hash


def cancel_detail_retry(
    self,
    source_key: str,
    posting_id: str,
    now: datetime,
) -> bool:
    source = self.state["sources"][source_key]
    removed = source["detail_retry_ids"].pop(posting_id, None)
    if removed is None:
        return False
    source["record_updated_at"] = now.astimezone(timezone.utc).isoformat()
    self.dirty = True
    return True
```

`StateManager.mark_source_seeded(source_key, seeded_at)` first verifies `last_complete_at` exists, then stores `seeded_at` only if absent. The global `meta.seeded_at` is informational; activation and alert eligibility use every enabled source's own marker. Required-source seed completeness is enforced again by the deployment gate.

`StateManager.observe_candidate()` resolves aliases, creates or updates the compact record described in Task 3, attaches the `source_key|posting_id` reference, resets that reference's omission state, and tracks a reopen without discarding `last_closed_at`. Before creating a record with no active candidate match, it calls `max_delivered_generation(durable_identity_aliases(job), delivered_receipts)`. That helper returns `-1` when no retained receipt intersects and otherwise returns the greatest matching generation, so a new record starts at exactly the maximum plus one. For an existing closed record, compare the incoming durable `source:` and trusted `req:` aliases with the aliases in the pre-observation snapshot. A newly attached durable alias increments `reopen_generation` immediately. With no new durable alias, a same-ID reopening increments only after `self.revision_policy.same_id_reopen_days`; a shorter reopen keeps the generation unchanged, and a material content hash still creates a distinct revision. Attaching an alias to an active record never increments the generation or alerts by itself. `observe_candidate()` changes `last_seen_at` only for a new reference or alias, reopen, or material content change; seeing an identical active record is inert.

`record_assessment()` compares compact semantic fields before mutation and does not rewrite timestamps on an unchanged assessment. When `discovered_during_seed` is true and `seed_baseline` is null, the first compact assessment is copied into `seed_baseline` exactly once. No later assessment may replace or clear it. `last_evaluation` remains the latest observational snapshot, while `comparison_basis()` ignores it. `record_alert_basis()` runs only after `queue_immediate()` or `queue_moderate()` returns true, so failed eligibility and duplicate queues never advance the comparison baseline. The caller must invoke `should_alert_revision()` against the candidate snapshot captured before `record_assessment()`. For a migrated candidate with `migration_baseline_pending`, compare the first live assessment against `migration_snapshot`. An equivalent company/title/location/URL assessment calls the separate `record_migration_baseline()` method to clear the flag, remove `migration_snapshot`, replace its legacy-local alias with the real source alias, and establish `last_alert_basis` without queueing. A material difference remains eligible under the normal revision rules. Seed mode calls `record_assessment()` for its immutable baseline but never calls `record_alert_basis()`.

Add these methods in `src/state.py` with exact signatures. Import `Collection` and `Mapping` from `collections.abc`, `Any` from `typing`, and `deepcopy` from `copy`:

```python
def max_delivered_generation(
    aliases: Collection[str],
    delivered_receipts: Mapping[str, Mapping[str, Any]],
) -> int:
    durable = set(aliases)
    return max(
        (
            int(receipt["reopen_generation"])
            for receipt in delivered_receipts.values()
            if durable.intersection(receipt.get("identity_aliases", ()))
        ),
        default=-1,
    )


def observe_candidate(
    self,
    job: Job,
    now: datetime,
    *,
    discovered_during_seed: bool,
) -> tuple[str, dict | None, dict]:
    previous = find_candidate_snapshot(job, self.state["candidates"])
    candidate_id = resolve_candidate_id(job, self.state["candidates"])
    prior_generation = max_delivered_generation(
        durable_identity_aliases(job), self.state["delivery"]["delivered"]
    )
    record, changed = upsert_candidate_record(
        self.state["candidates"],
        candidate_id,
        job,
        now,
        discovered_during_seed=discovered_during_seed,
        initial_reopen_generation=(prior_generation + 1 if previous is None else None),
        revision_policy=self.revision_policy,
    )
    self.dirty = self.dirty or changed
    return candidate_id, deepcopy(previous), deepcopy(record)

def record_assessment(self, assessment: JobAssessment, assessed_at: str) -> None:
    record = self.state["candidates"][assessment.candidate_id]
    snapshot = compact_assessment_snapshot(assessment, assessed_at)
    changed = set_compact_assessment_if_semantically_changed(record, snapshot)
    if record.get("discovered_during_seed") and record.get("seed_baseline") is None:
        record["seed_baseline"] = deepcopy(snapshot)
        changed = True
    self.dirty = self.dirty or changed

def record_migration_baseline(self, assessment: JobAssessment, assessed_at: str) -> None:
    record = self.state["candidates"][assessment.candidate_id]
    if not record.get("migration_baseline_pending"):
        raise ValueError("candidate has no pending migration baseline")
    replace_legacy_alias_and_store_baseline(record, assessment, assessed_at)
    self.dirty = True

def record_alert_basis(self, assessment: JobAssessment, queued_at: str) -> None:
    record = self.state["candidates"][assessment.candidate_id]
    snapshot = compact_assessment_snapshot(assessment, queued_at)
    changed = set_alert_basis_if_semantically_changed(record, snapshot, assessment.revision_id)
    invalidation = record.get("last_queue_invalidation") or {}
    remaining = sorted(
        set(invalidation.get("revision_ids", ())) - {assessment.revision_id}
    )
    if len(remaining) != len(invalidation.get("revision_ids", ())):
        record["last_queue_invalidation"] = (
            {**invalidation, "revision_ids": remaining} if remaining else None
        )
        changed = True
    self.dirty = self.dirty or changed
```

On a complete result, increment omission counts for prior IDs missing from the new set. Close a candidate on the second consecutive omission. Reset the omission count whenever the ID reappears. Preserve prior inventory on 304 and on every incomplete result.

- [ ] **Step 5: Implement canonical and probable duplicate keys**

In `src/dedupe.py`, normalize URL host casing, remove fragments, remove trailing slashes, and discard `utm_*`, `gh_src`, `lever-source`, and `source` query keys. `Job.source_key` is always the complete state-source key returned by `source_key(company)`, including the fetcher prefix and every identity-bearing adapter component; adapters, retry APIs, source references, and aliases never use a bare slug. `candidate_aliases(job)` returns a trusted `req:<normalized-company>:<requisition-id>` only when the employer requisition has official provenance, a normalized `url:<official-url>` alias when the URL is nonempty and valid, and the fallback `source:<job.source_key>:<job-id>`. `durable_identity_aliases(job: Job) -> tuple[str, ...]` returns the sorted subset of `candidate_aliases(job)` whose prefixes are `req:` or `source:`; it never returns `url:` aliases. `resolve_candidate_id()` first reuses a trusted requisition alias; it may reuse a URL alias only while the matching candidate is active; otherwise it chooses the source-scoped alias as the new canonical ID. It never auto-merges equal local ATS job IDs from different source keys. `canonical_job_key(job)` returns that resolved candidate ID when given the current candidate index and otherwise returns the first safe alias. `probable_duplicate_key()` must normalize whitespace, punctuation, common state names to abbreviations, company, title, and location, but must never be used to auto-merge records.

- [ ] **Step 6: Run the focused tests**

```bash
python -m pytest tests/test_source_lifecycle.py tests/test_dedupe.py -v
```

Expected: all lifecycle and deduplication tests pass.

- [ ] **Step 7: Commit lifecycle behavior**

```bash
git add src/state.py src/source_health.py src/lifecycle.py src/dedupe.py tests/test_source_lifecycle.py tests/test_dedupe.py
git commit -m "feat: track source lifecycle safely"
```

---

### Task 5: Replace the misleading health report

**Files:**
- Modify: `src/health.py:1-75`
- Create: `src/config.py`
- Create: `tests/test_health.py`

**Interfaces:**
- Consumes: version-2 source health and circuit records from Tasks 3 and 4
- Produces: `load_config(path)`, `build_health_report(config, state) -> tuple[str, bool]`, and a CLI that returns nonzero for failed required sources

- [ ] **Step 1: Write failing report tests**

Create `tests/test_health.py` with one healthy source, one empty-valid source, one required partial open-circuit source, one nonrequired best-effort failed source, one disabled source, and one missing configured required source. Assert the report never labels zero jobs healthy unless health is `empty-valid`, reports fetch health and circuit separately, counts every enabled source, including a missing state record, as monitored, counts disabled sources separately, and makes a partial/failed/open/missing `required_for_validation: true` source affect `has_required_failure`. A nonrequired best-effort failure remains visible without blocking validation.

```python
def test_health_report_separates_fetch_health_from_circuit(sample_config, sample_state):
    report, has_required_failure = build_health_report(sample_config, sample_state)
    assert "Figure | health=healthy | circuit=closed" in report
    assert "Tesla | health=failed | circuit=open" in report
    assert "Apple | disabled" in report
    assert "5 monitored, 1 disabled" in report
    assert has_required_failure is True
```

- [ ] **Step 2: Run the report test and verify it fails**

```bash
python -m pytest tests/test_health.py -v
```

Expected: failure because `build_health_report()` does not exist and the current report uses version-1 failure counts.

- [ ] **Step 3: Implement deterministic health rendering**

Create `src/config.py` with `load_config(path)` that loads YAML, requires a top-level mapping with a `companies` list, and raises `ConfigError` for missing, invalid, or wrong-shaped data. Make `build_health_report()` sort by configured company name and render company, fetch health, circuit state, active count, last complete timestamp, next probe, requirement flag, and the latest sanitized warning. The CLI exits `0` when every `required_for_validation: true` source is healthy or empty-valid, `1` when one is partial, failed, or open, and `2` for configuration or state corruption. Nonrequired failures remain visible and can enter the daily priority summary without failing validation.

```python
def parse_args(argv: list[str] | None, default_config: str, default_state: str):
    parser = argparse.ArgumentParser(description="Job source health")
    parser.add_argument("--config", default=default_config)
    parser.add_argument("--state", default=default_state)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv, default_config="config.yaml", default_state="state.json")
    config = load_config(args.config)
    state = StateManager.load(args.state)
    report, has_required_failure = build_health_report(config, state.state)
    print(report)
    return 1 if has_required_failure else 0
```

- [ ] **Step 4: Run all foundation tests**

```bash
python -m pytest tests/test_models.py tests/test_http_client.py tests/test_fetcher_base.py tests/test_state_migration.py tests/test_state_persistence.py tests/test_dedupe_migration.py tests/test_source_lifecycle.py tests/test_dedupe.py tests/test_health.py -v
```

Expected: all foundation tests pass.

- [ ] **Step 5: Commit the truthful health report**

```bash
git add src/config.py src/health.py tests/test_health.py
git commit -m "feat: report source health and circuit state"
```

## Foundation Verification Gate

Run:

```bash
python -m pytest -q
python -m compileall -q src tests
python -m src.health --help
git status --short
```

Expected: the full available suite passes, and the worktree contains no uncommitted files before starting the source-adapter plan.
