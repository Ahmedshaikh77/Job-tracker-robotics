from __future__ import annotations

from unittest.mock import Mock

import pytest
import requests

from src.fetchers.gem import GemFetcher
from src.fetchers.http import JsonResponse
from src.fetchers.smartrecruiters import SmartRecruitersFetcher
from src.models import DetailStatus, EmploymentType, FactSource, FetchContext, FetchHealth
from tests.fakes import FakeHttp, load_json_fixture


def http_error(status: int) -> requests.HTTPError:
    return requests.HTTPError(
        "official endpoint unavailable", response=Mock(status_code=status)
    )


def test_smartrecruiters_paginates_to_total_and_enriches_detail(
    fetch_context, monkeypatch
):
    monkeypatch.setattr(SmartRecruitersFetcher, "PAGE_SIZE", 1)
    company = {
        "name": "Intuitive",
        "fetcher": "smartrecruiters",
        "slug": "Intuitive",
    }
    http = FakeHttp(
        [
            load_json_fixture("smartrecruiters_page_1.json"),
            load_json_fixture("smartrecruiters_page_2.json"),
            load_json_fixture("smartrecruiters_detail.json"),
        ]
    )
    fetcher = SmartRecruitersFetcher(http)

    result = fetcher.fetch(company, fetch_context)
    detail = fetcher.fetch_detail(company, result.jobs[0])

    assert detail.status is DetailStatus.HEALTHY
    assert result.source_total == 2
    assert result.total_is_authoritative is True
    assert result.pages_fetched == 2
    assert detail.job.description == (
        "Full-time robotics validation role.\n\n"
        "Two years of hardware integration experience."
    )
    assert detail.job.url == "https://jobs.smartrecruiters.com/Intuitive/sr-1/apply"
    assert detail.job.employment_type is EmploymentType.FULL_TIME
    assert detail.job.country_code == "US"
    assert detail.job.provenance["description"] is FactSource.OFFICIAL_DETAIL


def test_smartrecruiters_total_drift_is_partial(fetch_context, monkeypatch):
    monkeypatch.setattr(SmartRecruitersFetcher, "PAGE_SIZE", 1)
    second = load_json_fixture("smartrecruiters_page_2.json")
    second["totalFound"] = 3
    result = SmartRecruitersFetcher(
        FakeHttp([load_json_fixture("smartrecruiters_page_1.json"), second])
    ).fetch(
        {"name": "Intuitive", "fetcher": "smartrecruiters", "slug": "Intuitive"},
        fetch_context,
    )
    assert result.health is FetchHealth.PARTIAL
    assert result.complete is False


def test_smartrecruiters_premature_short_page_is_partial(fetch_context):
    page = load_json_fixture("smartrecruiters_page_1.json")
    page["totalFound"] = 2
    result = SmartRecruitersFetcher(FakeHttp([page])).fetch(
        {"name": "Intuitive", "fetcher": "smartrecruiters", "slug": "Intuitive"},
        fetch_context,
    )
    assert result.health is FetchHealth.PARTIAL


def test_smartrecruiters_detail_falls_back_to_posting_url(fetch_context, monkeypatch):
    monkeypatch.setattr(SmartRecruitersFetcher, "PAGE_SIZE", 2)
    list_page = load_json_fixture("smartrecruiters_page_1.json")
    list_page["totalFound"] = 1
    detail = load_json_fixture("smartrecruiters_detail.json")
    detail.pop("applyUrl")
    company = {
        "name": "Intuitive",
        "fetcher": "smartrecruiters",
        "slug": "Intuitive",
    }
    http = FakeHttp([list_page, detail])
    fetcher = SmartRecruitersFetcher(http)
    job = fetcher.fetch(company, fetch_context).jobs[0]
    result = fetcher.fetch_detail(company, job)
    assert result.job.url == "https://jobs.smartrecruiters.com/Intuitive/sr-1"


@pytest.mark.parametrize(
    ("status", "expected"),
    [(404, DetailStatus.CLOSED), (410, DetailStatus.CLOSED), (503, DetailStatus.FAILED)],
)
def test_smartrecruiters_detail_distinguishes_closure(make_job, status, expected):
    job = make_job(
        source_type="smartrecruiters",
        source_key="smartrecruiters:Intuitive",
        job_id="sr-1",
    )
    result = SmartRecruitersFetcher(FakeHttp([http_error(status)])).fetch_detail(
        {"name": "Intuitive", "fetcher": "smartrecruiters", "slug": "Intuitive"},
        job,
    )
    assert result.status is expected


def test_gem_maps_authoritative_dates_and_employment_type(fetch_context):
    company = {"name": "Chef Robotics", "fetcher": "gem", "slug": "chef-robotics"}
    http = FakeHttp(
        [load_json_fixture("gem_jobs.json"), load_json_fixture("gem_job_detail.json")]
    )
    fetcher = GemFetcher(http)

    result = fetcher.fetch(company, fetch_context)
    detail = fetcher.fetch_detail(company, result.jobs[0])

    job = result.jobs[0]
    assert detail.status is DetailStatus.HEALTHY
    assert job.posted_at == "2026-09-05T12:00:00Z"
    assert job.updated_at == "2026-09-07T12:00:00Z"
    assert job.employment_type is EmploymentType.FULL_TIME
    assert job.source_key == "gem:chef-robotics"
    assert result.total_is_authoritative is False


def test_gem_explicit_empty_is_authoritative(fetch_context):
    result = GemFetcher(FakeHttp([[]])).fetch(
        {"name": "Chef Robotics", "fetcher": "gem", "slug": "chef-robotics"},
        fetch_context,
    )
    assert result.health is FetchHealth.EMPTY_VALID
    assert result.total_is_authoritative is True


def test_gem_304_reuses_prior_and_force_full_omits_etag():
    company = {"name": "Chef Robotics", "fetcher": "gem", "slug": "chef-robotics"}
    context = FetchContext(
        previous_etag='"gem"', previous_active_ids=frozenset({"gem-1"})
    )
    cached = FakeHttp([JsonResponse(None, 304, '"gem"', True)])
    result = GemFetcher(cached).fetch(company, context)
    assert result.unchanged is True
    assert cached.calls[0].etag == '"gem"'

    full = FakeHttp([load_json_fixture("gem_jobs.json")])
    GemFetcher(full).fetch(
        company, FetchContext(previous_etag='"gem"', force_full=True)
    )
    assert full.calls[0].etag is None


@pytest.mark.parametrize(
    "mutation",
    [
        lambda row: row.__setitem__("id", "wrong"),
        lambda row: (row.pop("content_plain"), row.pop("content")),
        lambda row: row.__setitem__("absolute_url", ""),
    ],
)
def test_gem_detail_rejects_schema_mismatch(make_job, mutation):
    payload = load_json_fixture("gem_job_detail.json")
    mutation(payload)
    job = make_job(
        source_type="gem", source_key="gem:chef-robotics", job_id="gem-1"
    )
    result = GemFetcher(FakeHttp([payload])).fetch_detail(
        {"name": "Chef Robotics", "fetcher": "gem", "slug": "chef-robotics"},
        job,
    )
    assert result.status is DetailStatus.FAILED


@pytest.mark.parametrize(
    ("status", "expected"),
    [(404, DetailStatus.CLOSED), (410, DetailStatus.CLOSED), (403, DetailStatus.FAILED)],
)
def test_gem_detail_distinguishes_closure(make_job, status, expected):
    result = GemFetcher(FakeHttp([http_error(status)])).fetch_detail(
        {"name": "Chef Robotics", "fetcher": "gem", "slug": "chef-robotics"},
        make_job(source_type="gem", source_key="gem:chef-robotics", job_id="gem-1"),
    )
    assert result.status is expected
