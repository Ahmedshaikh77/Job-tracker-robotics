from __future__ import annotations

from unittest.mock import Mock

import pytest
import requests

from src.fetchers.rippling import RipplingFetcher
from src.fetchers.workday import WorkdayFetcher
from src.models import DetailStatus, EmploymentType, FactSource, FetchContext, FetchHealth
from tests.fakes import FakeHttp, load_json_fixture


WORKDAY_COMPANY = {
    "name": "Boston Dynamics",
    "fetcher": "workday",
    "host": "bostondynamics.wd1.myworkdayjobs.com",
    "tenant": "bostondynamics",
    "site": "Boston_Dynamics",
}
RIPPLING_COMPANY = {
    "name": "Foundation Robotics",
    "fetcher": "rippling",
    "board": "foundation-robotics",
}


def http_error(status: int) -> requests.HTTPError:
    return requests.HTTPError(
        "official endpoint unavailable", response=Mock(status_code=status)
    )


def test_workday_uses_total_and_detail_start_date(fetch_context, monkeypatch):
    monkeypatch.setattr(WorkdayFetcher, "PAGE_SIZE", 1)
    http = FakeHttp(
        [
            load_json_fixture("workday_list_page_1.json"),
            load_json_fixture("workday_list_page_2.json"),
            load_json_fixture("workday_detail.json"),
        ]
    )
    fetcher = WorkdayFetcher(http)

    result = fetcher.fetch(WORKDAY_COMPANY, fetch_context)
    detail = fetcher.fetch_detail(WORKDAY_COMPANY, result.jobs[0])

    assert detail.status is DetailStatus.HEALTHY
    assert result.complete is True
    assert result.source_total == 2
    assert result.total_is_authoritative is True
    assert result.pages_fetched == 2
    assert detail.job.posted_at == "2026-09-04"
    assert detail.job.employment_type is EmploymentType.FULL_TIME
    assert detail.job.requisition_id == "R12345"
    assert detail.job.country_code == "US"
    assert "sponsorship" in detail.job.description.lower()
    assert detail.job.provenance["description"] is FactSource.OFFICIAL_DETAIL


def test_workday_later_page_failure_is_partial_and_preserves_prior(monkeypatch):
    monkeypatch.setattr(WorkdayFetcher, "PAGE_SIZE", 1)
    context = FetchContext(previous_active_ids=frozenset({"old"}))
    result = WorkdayFetcher(
        FakeHttp([load_json_fixture("workday_list_page_1.json"), http_error(503)])
    ).fetch(WORKDAY_COMPANY, context)
    assert result.health is FetchHealth.PARTIAL
    assert result.active_ids == context.previous_active_ids


def test_workday_total_drift_is_partial(fetch_context, monkeypatch):
    monkeypatch.setattr(WorkdayFetcher, "PAGE_SIZE", 1)
    second = load_json_fixture("workday_list_page_2.json")
    second["total"] = 3
    result = WorkdayFetcher(
        FakeHttp([load_json_fixture("workday_list_page_1.json"), second])
    ).fetch(WORKDAY_COMPANY, fetch_context)
    assert result.health is FetchHealth.PARTIAL


def test_workday_accepts_captured_nonfirst_zero_total_sentinel(
    fetch_context, monkeypatch
):
    monkeypatch.setattr(WorkdayFetcher, "PAGE_SIZE", 1)
    first = load_json_fixture("workday_list_page_1.json")
    first["total"] = 3
    second = load_json_fixture("workday_list_page_2.json")
    second["total"] = 0
    third = load_json_fixture("workday_list_page_2.json")
    third["total"] = 0
    third["jobPostings"][0]["title"] = "Controls Engineer I"
    third["jobPostings"][0]["externalPath"] = (
        "/job/Waltham-MA/Controls-Engineer-I_R99999"
    )

    result = WorkdayFetcher(
        FakeHttp([first, second, third])
    ).fetch(WORKDAY_COMPANY, fetch_context)

    assert result.health is FetchHealth.HEALTHY
    assert result.complete is True
    assert result.source_total == 3
    assert result.total_is_authoritative is True
    assert result.pages_fetched == 3


def test_workday_invalid_path_identity_is_partial(fetch_context):
    page = load_json_fixture("workday_list_page_1.json")
    page["total"] = 1
    page["jobPostings"][0]["externalPath"] = "/job/no-opaque-requisition"
    result = WorkdayFetcher(FakeHttp([page])).fetch(WORKDAY_COMPANY, fetch_context)
    assert result.health is FetchHealth.PARTIAL


def test_workday_explicit_zero_is_empty_valid(fetch_context):
    result = WorkdayFetcher(FakeHttp([{"total": 0, "jobPostings": []}])).fetch(
        WORKDAY_COMPANY, fetch_context
    )
    assert result.health is FetchHealth.EMPTY_VALID


@pytest.mark.parametrize(
    ("status", "expected"),
    [(404, DetailStatus.CLOSED), (410, DetailStatus.CLOSED), (403, DetailStatus.FAILED)],
)
def test_workday_detail_distinguishes_closure(make_job, status, expected):
    job = make_job(
        source_type="workday",
        source_key=(
            "workday:bostondynamics.wd1.myworkdayjobs.com:"
            "bostondynamics:Boston_Dynamics"
        ),
        job_id="R12345",
        metadata={"external_path": "/job/Test_R12345"},
    )
    detail = WorkdayFetcher(FakeHttp([http_error(status)])).fetch_detail(
        WORKDAY_COMPANY, job
    )
    assert detail.status is expected


def test_workday_inactive_200_is_failed_not_closed(make_job):
    payload = load_json_fixture("workday_detail.json")
    payload["jobPostingInfo"]["canApply"] = False
    job = make_job(
        source_type="workday",
        source_key=(
            "workday:bostondynamics.wd1.myworkdayjobs.com:"
            "bostondynamics:Boston_Dynamics"
        ),
        job_id="R12345",
        metadata={
            "external_path": "/job/Waltham-MA/Robotics-Test-Engineer-I_R12345"
        },
    )
    detail = WorkdayFetcher(FakeHttp([payload])).fetch_detail(WORKDAY_COMPANY, job)
    assert detail.status is DetailStatus.FAILED
    assert "withheld" in detail.error


def test_rippling_requires_expected_list_and_detail_shapes(fetch_context):
    http = FakeHttp(
        [load_json_fixture("rippling_jobs.json"), load_json_fixture("rippling_detail.json")]
    )
    fetcher = RipplingFetcher(http)

    result = fetcher.fetch(RIPPLING_COMPANY, fetch_context)
    detail = fetcher.fetch_detail(RIPPLING_COMPANY, result.jobs[0])

    assert detail.status is DetailStatus.HEALTHY
    assert detail.job.posted_at == "2026-09-03T12:00:00Z"
    assert detail.job.employment_type is EmploymentType.FULL_TIME
    assert detail.job.salary is None
    assert detail.job.source_key == "rippling:foundation-robotics"


def test_rippling_aggregates_repeated_locations(fetch_context):
    result = RipplingFetcher(
        FakeHttp([load_json_fixture("rippling_jobs_repeated_location.json")])
    ).fetch(RIPPLING_COMPANY, fetch_context)

    assert len(result.jobs) == 1
    assert result.active_ids == frozenset({"rip-1"})
    assert result.source_total == 1
    assert result.total_is_authoritative is False
    assert result.jobs[0].metadata["locations"] == (
        {"id": "sf", "label": "San Francisco, CA"},
        {"id": "sj", "label": "San Jose, CA"},
    )


def test_rippling_wrong_top_level_shape_fails(fetch_context):
    result = RipplingFetcher(FakeHttp([{"jobs": []}])).fetch(
        RIPPLING_COMPANY, fetch_context
    )
    assert result.health is FetchHealth.FAILED


def test_rippling_unlisted_200_is_failed_not_closed(make_job):
    payload = load_json_fixture("rippling_detail.json")
    payload["unlistedFromSearch"] = True
    result = RipplingFetcher(FakeHttp([payload])).fetch_detail(
        RIPPLING_COMPANY,
        make_job(
            source_type="rippling",
            source_key="rippling:foundation-robotics",
            job_id="rip-1",
        ),
    )
    assert result.status is DetailStatus.FAILED
    assert "not publicly listed" in result.error


@pytest.mark.parametrize(
    ("status", "expected"),
    [(404, DetailStatus.CLOSED), (410, DetailStatus.CLOSED), (500, DetailStatus.FAILED)],
)
def test_rippling_detail_distinguishes_closure(make_job, status, expected):
    result = RipplingFetcher(FakeHttp([http_error(status)])).fetch_detail(
        RIPPLING_COMPANY,
        make_job(
            source_type="rippling",
            source_key="rippling:foundation-robotics",
            job_id="rip-1",
        ),
    )
    assert result.status is expected
