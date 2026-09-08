from __future__ import annotations

from decimal import Decimal
from unittest.mock import Mock

import pytest
import requests

from src.fetchers.ashby import AshbyFetcher
from src.fetchers.greenhouse import GreenhouseFetcher
from src.fetchers.http import JsonResponse
from src.fetchers.lever import LeverFetcher
from src.models import (
    DetailStatus,
    EmploymentType,
    FactSource,
    FetchContext,
    FetchHealth,
    WorkplaceType,
)
from tests.fakes import FakeHttp, load_json_fixture


def http_error(status: int) -> requests.HTTPError:
    response = Mock(status_code=status)
    return requests.HTTPError("official endpoint unavailable", response=response)


def test_greenhouse_uses_update_only_as_updated_at(fetch_context):
    http = FakeHttp([load_json_fixture("greenhouse_jobs.json")])

    result = GreenhouseFetcher(http).fetch(
        {"name": "Figure", "fetcher": "greenhouse", "slug": "figureai"},
        fetch_context,
    )

    job = result.jobs[0]
    assert result.complete is True
    assert result.total_is_authoritative is True
    assert result.active_ids == frozenset({"123"})
    assert job.posted_at is None
    assert job.updated_at == "2026-09-07T10:00:00-04:00"
    assert job.source_key == "greenhouse:figureai"
    assert job.country_code == "US"
    assert job.provenance["description"] is FactSource.STRUCTURED_FEED


def test_greenhouse_304_reuses_prior_inventory_and_force_full_omits_etag():
    prior = FetchContext(previous_etag='"old"', previous_active_ids=frozenset({"123"}))
    cached_http = FakeHttp([JsonResponse(None, 304, '"old"', True)])

    result = GreenhouseFetcher(cached_http).fetch(
        {"name": "Figure", "fetcher": "greenhouse", "slug": "figureai"}, prior
    )

    assert result.unchanged is True
    assert result.active_ids == prior.previous_active_ids
    assert cached_http.calls[0].etag == '"old"'

    full_http = FakeHttp([load_json_fixture("greenhouse_jobs.json")])
    GreenhouseFetcher(full_http).fetch(
        {"name": "Figure", "fetcher": "greenhouse", "slug": "figureai"},
        FetchContext(previous_etag='"old"', force_full=True),
    )
    assert full_http.calls[0].etag is None


def test_greenhouse_total_mismatch_is_partial(fetch_context):
    http = FakeHttp([{"meta": {"total": 1}, "jobs": []}])
    result = GreenhouseFetcher(http).fetch(
        {"name": "Figure", "fetcher": "greenhouse", "slug": "figureai"},
        fetch_context,
    )
    assert result.health is FetchHealth.PARTIAL
    assert result.complete is False
    assert result.active_ids == fetch_context.previous_active_ids


def test_greenhouse_explicit_zero_is_empty_valid(fetch_context):
    result = GreenhouseFetcher(FakeHttp([{"meta": {"total": 0}, "jobs": []}])).fetch(
        {"name": "Figure", "fetcher": "greenhouse", "slug": "figureai"},
        fetch_context,
    )
    assert result.health is FetchHealth.EMPTY_VALID
    assert result.total_is_authoritative is True


def test_greenhouse_detail_enriches_dates_and_provenance(fetch_context):
    http = FakeHttp(
        [
            load_json_fixture("greenhouse_jobs.json"),
            load_json_fixture("greenhouse_job_detail.json"),
        ]
    )
    company = {"name": "Figure", "fetcher": "greenhouse", "slug": "figureai"}
    fetcher = GreenhouseFetcher(http)
    job = fetcher.fetch(company, fetch_context).jobs[0]

    detail = fetcher.fetch_detail(company, job)

    assert detail.status is DetailStatus.HEALTHY
    assert detail.job.posted_at == "2026-09-05T09:00:00-04:00"
    assert detail.job.provenance["posted_at"] is FactSource.OFFICIAL_DETAIL


@pytest.mark.parametrize(
    ("status", "expected"),
    [(404, DetailStatus.CLOSED), (410, DetailStatus.CLOSED), (403, DetailStatus.FAILED)],
)
def test_greenhouse_detail_distinguishes_closure_from_failure(
    fetch_context, make_job, status, expected
):
    detail = GreenhouseFetcher(FakeHttp([http_error(status)])).fetch_detail(
        {"name": "Figure", "fetcher": "greenhouse", "slug": "figureai"},
        make_job(),
    )
    assert detail.status is expected
    assert detail.job is None


def test_ashby_keeps_compensation_and_full_time(fetch_context):
    company = {"name": "Applied Intuition", "fetcher": "ashby", "slug": "applied"}
    fetcher = AshbyFetcher(FakeHttp([load_json_fixture("ashby_jobs.json")]))

    result = fetcher.fetch(company, fetch_context)

    job = result.jobs[0]
    assert job.employment_type is EmploymentType.FULL_TIME
    assert job.workplace_type is WorkplaceType.ONSITE
    assert job.salary.annual_minimum == Decimal("120000")
    assert job.salary.currency == "USD"
    assert result.total_is_authoritative is False
    assert fetcher.fetch_detail(company, job).status is DetailStatus.HEALTHY


def test_ashby_detail_rejects_a_job_not_certified_by_board(make_job):
    detail = AshbyFetcher(FakeHttp()).fetch_detail(
        {"name": "Applied Intuition", "fetcher": "ashby", "slug": "applied"},
        make_job(source_type="ashby", source_key="ashby:applied"),
    )
    assert detail.status is DetailStatus.FAILED


def test_ashby_explicit_empty_and_missing_collection_differ(fetch_context):
    company = {"name": "Applied Intuition", "fetcher": "ashby", "slug": "applied"}
    empty = AshbyFetcher(FakeHttp([{"jobs": []}])).fetch(company, fetch_context)
    missing = AshbyFetcher(FakeHttp([{}])).fetch(company, fetch_context)
    assert empty.health is FetchHealth.EMPTY_VALID
    assert missing.health is FetchHealth.FAILED


def test_ashby_force_full_omits_etag():
    http = FakeHttp([load_json_fixture("ashby_jobs.json")])
    AshbyFetcher(http).fetch(
        {"name": "Applied Intuition", "fetcher": "ashby", "slug": "applied"},
        FetchContext(previous_etag='"old"', force_full=True),
    )
    assert http.calls[0].etag is None


def test_lever_reads_until_short_page_and_uses_fingerprint(fetch_context, monkeypatch):
    monkeypatch.setattr(LeverFetcher, "PAGE_SIZE", 1)
    http = FakeHttp(
        [
            load_json_fixture("lever_jobs_page_1.json"),
            load_json_fixture("lever_jobs_page_2.json"),
        ]
    )

    result = LeverFetcher(http).fetch(
        {"name": "Zoox", "fetcher": "lever", "slug": "zoox"}, fetch_context
    )

    assert result.pages_fetched == 2
    assert result.complete is True
    assert result.total_is_authoritative is False
    assert result.fingerprint is not None
    assert result.jobs[0].url.endswith("/apply")
    assert result.jobs[0].posted_at is None


def test_lever_later_page_failure_is_partial_and_preserves_prior(monkeypatch):
    monkeypatch.setattr(LeverFetcher, "PAGE_SIZE", 1)
    context = FetchContext(previous_active_ids=frozenset({"old"}))
    http = FakeHttp([load_json_fixture("lever_jobs_page_1.json"), http_error(503)])

    result = LeverFetcher(http).fetch(
        {"name": "Zoox", "fetcher": "lever", "slug": "zoox"}, context
    )

    assert result.health is FetchHealth.PARTIAL
    assert result.complete is False
    assert result.active_ids == context.previous_active_ids


def test_lever_duplicate_page_cannot_complete(monkeypatch, fetch_context):
    monkeypatch.setattr(LeverFetcher, "PAGE_SIZE", 1)
    page = load_json_fixture("lever_jobs_page_1.json")
    result = LeverFetcher(FakeHttp([page, page])).fetch(
        {"name": "Zoox", "fetcher": "lever", "slug": "zoox"}, fetch_context
    )
    assert result.health is FetchHealth.PARTIAL


def test_lever_detail_uses_official_apply_url(fetch_context):
    company = {"name": "Zoox", "fetcher": "lever", "slug": "zoox"}
    http = FakeHttp(
        [
            load_json_fixture("lever_jobs_page_1.json"),
            load_json_fixture("lever_job_detail.json"),
        ]
    )
    fetcher = LeverFetcher(http)
    job = fetcher.fetch(company, fetch_context).jobs[0]

    detail = fetcher.fetch_detail(company, job)

    assert detail.status is DetailStatus.HEALTHY
    assert detail.job.url == "https://jobs.lever.co/zoox/lever-1/apply"
    assert detail.job.provenance["description"] is FactSource.OFFICIAL_DETAIL


@pytest.mark.parametrize(
    ("status", "expected"),
    [(404, DetailStatus.CLOSED), (410, DetailStatus.CLOSED), (429, DetailStatus.FAILED)],
)
def test_lever_detail_distinguishes_closure_from_failure(make_job, status, expected):
    detail = LeverFetcher(FakeHttp([http_error(status)])).fetch_detail(
        {"name": "Zoox", "fetcher": "lever", "slug": "zoox"},
        make_job(source_type="lever", source_key="lever:zoox", job_id="lever-1"),
    )
    assert detail.status is expected
