from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from src.config import (
    AppSettings,
    DigestPolicy,
    FetchPolicy,
    MatchingPolicy,
    SourceConfig,
    StatePolicy,
    TelegramPolicy,
)
from src.models import (
    DetailResult,
    DetailStatus,
    FetchContext,
    FetchHealth,
    FetchResult,
    Job,
)
from src.verify import run_verification


@dataclass
class FakeState:
    state: dict

    def fetch_context(self, source_key, now):
        return FetchContext(previous_active_ids=frozenset({"old"}))


class FixedFetcher:
    def __init__(self, result):
        self.result = result

    def fetch(self, company, context):
        return self.result


class DetailFetcher(FixedFetcher):
    def __init__(self, result, detail):
        super().__init__(result)
        self.detail = detail
        self.detail_jobs = []

    def fetch_detail(self, company, job):
        self.detail_jobs.append(job)
        return self.detail


def _result(health: FetchHealth) -> FetchResult:
    if health is FetchHealth.EMPTY_VALID:
        return FetchResult(
            jobs=(),
            active_ids=frozenset(),
            complete=True,
            source_total=0,
            pages_fetched=1,
            fetched_at="2026-09-07T12:00:00+00:00",
            health=health,
            total_is_authoritative=True,
        )
    return FetchResult(
        jobs=(),
        active_ids=frozenset({"old"}),
        complete=False,
        source_total=None,
        pages_fetched=0,
        fetched_at="2026-09-07T12:00:00+00:00",
        health=health,
        error="source unavailable",
    )


def _settings() -> AppSettings:
    return AppSettings(
        FetchPolicy(),
        StatePolicy(),
        MatchingPolicy(),
        DigestPolicy(),
        TelegramPolicy(),
        (
            SourceConfig("Required", "greenhouse", True, "validated", "high", True, slug="required"),
            SourceConfig("Tesla", "tesla", True, "best-effort", "high", False, board="careers"),
        ),
    )


def test_verification_ignores_nonrequired_failure_for_exit_status():
    results = {
        "greenhouse": _result(FetchHealth.EMPTY_VALID),
        "tesla": _result(FetchHealth.FAILED),
    }
    text, status = run_verification(
        _settings(),
        FakeState({"sources": {}}),
        fetcher_factory=lambda name, http=None: FixedFetcher(results[name]),
        now=datetime(2026, 9, 7, 16, 0, tzinfo=timezone.utc),
    )
    assert status == 0
    assert "Required | health=empty-valid | complete=true | pages=1 | total=0" in text
    assert "Tesla | health=failed | complete=false" in text
    assert "warning=source unavailable" in text


def test_verification_required_partial_returns_one():
    results = {
        "greenhouse": _result(FetchHealth.PARTIAL),
        "tesla": _result(FetchHealth.FAILED),
    }
    _, status = run_verification(
        _settings(),
        FakeState({"sources": {}}),
        fetcher_factory=lambda name, http=None: FixedFetcher(results[name]),
        now=datetime(2026, 9, 7, 16, 0, tzinfo=timezone.utc),
    )
    assert status == 1


def test_verification_existing_open_required_circuit_returns_one():
    results = {
        "greenhouse": _result(FetchHealth.EMPTY_VALID),
        "tesla": _result(FetchHealth.FAILED),
    }
    state = FakeState(
        {"sources": {"greenhouse:required": {"circuit": "open"}}}
    )
    text, status = run_verification(
        _settings(),
        state,
        fetcher_factory=lambda name, http=None: FixedFetcher(results[name]),
        now=datetime(2026, 9, 7, 16, 0, tzinfo=timezone.utc),
    )
    assert status == 1
    assert "circuit=open" in text


def test_verification_samples_relevant_detail_and_requires_healthy_detail():
    jobs = (
        Job(
            source_type="greenhouse",
            source_key="greenhouse:required",
            company="Required",
            job_id="sales",
            title="Account Manager",
            location="New York, NY, US",
            url="https://boards.greenhouse.io/required/jobs/sales",
        ),
        Job(
            source_type="greenhouse",
            source_key="greenhouse:required",
            company="Required",
            job_id="robotics",
            title="Robotics Test Engineer",
            location="Boston, MA, US",
            url="https://boards.greenhouse.io/required/jobs/robotics",
        ),
    )
    result = FetchResult(
        jobs=jobs,
        active_ids=frozenset({"sales", "robotics"}),
        complete=True,
        source_total=2,
        pages_fetched=1,
        fetched_at="2026-09-07T12:00:00+00:00",
        health=FetchHealth.HEALTHY,
        total_is_authoritative=True,
    )
    detail = DetailResult(
        job=None,
        status=DetailStatus.FAILED,
        fetched_at="2026-09-07T12:00:01+00:00",
        error="official detail schema mismatch",
    )
    fetcher = DetailFetcher(result, detail)

    text, status = run_verification(
        _settings(),
        FakeState({"sources": {}}),
        fetcher_factory=lambda name, http=None: (
            fetcher if name == "greenhouse" else FixedFetcher(_result(FetchHealth.FAILED))
        ),
        now=datetime(2026, 9, 7, 16, 0, tzinfo=timezone.utc),
        detail_sample=True,
    )

    assert fetcher.detail_jobs == [jobs[1]]
    assert "detail=failed | title=Robotics Test Engineer" in text
    assert "detail_warning=official detail schema mismatch" in text
    assert status == 1
