from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from unittest.mock import Mock

import pytest
import requests

from src.fetchers.amazon import AmazonFetcher
from src.fetchers.http import TextResponse
from src.fetchers.tesla import TeslaFetcher
from src.models import DetailStatus, EmploymentType, FactSource, FetchContext, FetchHealth
from tests.fakes import FakeHttp, load_json_fixture, load_text_fixture


AMAZON_COMPANY = {
    "name": "Amazon Robotics",
    "fetcher": "amazon",
    "source_id": "robotics-us",
    "search_query": "robotics",
}
TESLA_COMPANY = {
    "name": "Tesla",
    "fetcher": "tesla",
    "board": "careers",
    "best_effort": True,
}


def http_error(status: int) -> requests.HTTPError:
    return requests.HTTPError(
        "official endpoint unavailable", response=Mock(status_code=status)
    )


def test_amazon_uses_effective_us_area_parameters(fetch_context):
    http = FakeHttp([load_json_fixture("amazon_page.json")])

    result = AmazonFetcher(http).fetch(AMAZON_COMPANY, fetch_context)

    params = http.calls[0].params
    assert params == {
        "base_query": "robotics",
        "country": "USA",
        "loc_query": "United States",
        "latitude": "38.89037",
        "longitude": "-77.03196",
        "type": "area",
        "result_limit": 100,
        "offset": 0,
        "sort": "recent",
    }
    assert result.complete is True


def test_amazon_normalizes_every_valid_row_so_inventory_and_jobs_agree(fetch_context):
    result = AmazonFetcher(FakeHttp([load_json_fixture("amazon_page.json")])).fetch(
        AMAZON_COMPANY, fetch_context
    )
    assert {job.country_code for job in result.jobs} == {"US", "LU"}
    assert result.source_total == 2
    assert result.total_is_authoritative is True
    assert result.active_ids == frozenset({"us-job", "lu-job"})
    assert {job.job_id for job in result.jobs} == result.active_ids


def test_amazon_uses_captured_top_level_location_aliases(fetch_context):
    result = AmazonFetcher(
        FakeHttp([load_json_fixture("amazon_page_live_location_alias.json")])
    ).fetch(AMAZON_COMPANY, fetch_context)

    assert result.complete is True
    job = result.jobs[0]
    assert job.location == "Westborough, Massachusetts, USA"
    assert job.city == "Westborough"
    assert job.region == "Massachusetts"
    assert job.country_code == "US"


def test_amazon_accepts_captured_semantic_detail_page(fetch_context):
    fetcher = AmazonFetcher(
        FakeHttp(
            [
                load_json_fixture("amazon_page_live_location_alias.json"),
                TextResponse(
                    load_text_fixture("amazon_detail_semantic.html"),
                    200,
                    None,
                    False,
                ),
            ]
        )
    )
    listed = fetcher.fetch(AMAZON_COMPANY, fetch_context).jobs[0]

    detail = fetcher.fetch_detail(AMAZON_COMPANY, listed)

    assert detail.status is DetailStatus.HEALTHY
    assert detail.job is not None
    assert detail.job.title == "Advanced Manufacturing Engineer"
    assert detail.job.location == "Westborough, MA, US"
    assert detail.job.city == "Westborough"
    assert detail.job.region == "MA"
    assert detail.job.country_code == "US"
    assert detail.job.posted_at == listed.posted_at
    assert "Two years of engineering experience." in detail.job.description
    assert detail.job.url == (
        "https://www.amazon.jobs/applicant/jobs/alias-job/apply"
    )


def test_amazon_accepts_captured_multiple_us_detail_locations(make_job):
    listed = make_job(
        source_type="amazon",
        source_key="amazon:robotics-us",
        company="Amazon Robotics",
        job_id="10445359",
        title="Electrical Test Engineer II, Hardware Test Engineering",
        location="North Reading, Massachusetts, USA",
        city="North Reading",
        region="Massachusetts",
        country_code="US",
        url=(
            "https://www.amazon.jobs/en/jobs/10445359/"
            "electrical-test-engineer-ii-hardware-test-engineering"
        ),
    )
    fetcher = AmazonFetcher(
        FakeHttp(
            [
                TextResponse(
                    load_text_fixture("amazon_detail_semantic_multi_location.html"),
                    200,
                    None,
                    False,
                )
            ]
        )
    )

    detail = fetcher.fetch_detail(AMAZON_COMPANY, listed)

    assert detail.status is DetailStatus.HEALTHY
    assert detail.job is not None
    assert detail.job.location == "North Reading, MA, US; Westboro, MA, US"
    assert detail.job.city == "North Reading"
    assert detail.job.region == "MA"
    assert detail.job.country_code == "US"
    assert detail.job.metadata["official_locations"] == (
        "North Reading, MA, US",
        "Westboro, MA, US",
    )
    assert detail.job.provenance["location"] is FactSource.OFFICIAL_DETAIL


def test_amazon_multiple_detail_locations_fail_closed_if_country_is_mixed(make_job):
    listed = make_job(
        source_type="amazon",
        source_key="amazon:robotics-us",
        job_id="10445359",
    )
    html = load_text_fixture("amazon_detail_semantic_multi_location.html").replace(
        "USA, MA, Westboro", "Canada, ON, Toronto"
    )

    detail = AmazonFetcher(
        FakeHttp([TextResponse(html, 200, None, False)])
    ).fetch_detail(AMAZON_COMPANY, listed)

    assert detail.status is DetailStatus.FAILED


@pytest.mark.parametrize(
    "old,new",
    [
        ("Job ID: alias-job", "Job ID: other-job"),
        ('id="apply-button"', 'id="other-button"'),
        ("Basic Qualifications", "Requirements"),
    ],
)
def test_amazon_semantic_detail_fails_closed_on_identity_or_shape_change(
    fetch_context, old, new
):
    listed = AmazonFetcher(
        FakeHttp([load_json_fixture("amazon_page_live_location_alias.json")])
    ).fetch(AMAZON_COMPANY, fetch_context).jobs[0]
    html = load_text_fixture("amazon_detail_semantic.html").replace(old, new)

    detail = AmazonFetcher(
        FakeHttp([TextResponse(html, 200, None, False)])
    ).fetch_detail(AMAZON_COMPANY, listed)

    assert detail.status is DetailStatus.FAILED


def _amazon_row(index: int) -> dict:
    return {
        "id_icims": f"job-{index}",
        "title": f"Robotics Engineer {index}",
        "job_path": f"/en/jobs/job-{index}/robotics-engineer",
        "posted_date": "September 5, 2026",
        "updated_time": "2026-09-06T12:00:00Z",
        "description": "Build robots.",
        "basic_qualifications": "Test systems.",
        "preferred_qualifications": "Python.",
        "location": "Sunnyvale, California, USA",
        "normalized_location": {
            "city": "Sunnyvale",
            "region": "CA",
            "country_code": "USA",
        },
    }


@pytest.mark.parametrize(
    ("raw_posted_date", "expected"),
    [
        ("September  3, 2026", "2026-09-03"),
        ("August 16, 2026", "2026-08-16"),
        ("April 16, 2026", "2026-04-16"),
    ],
)
def test_amazon_normalizes_official_human_posted_dates(
    fetch_context, raw_posted_date, expected
):
    row = _amazon_row(1)
    row["posted_date"] = raw_posted_date

    result = AmazonFetcher(FakeHttp([{"hits": 1, "jobs": [row]}])).fetch(
        AMAZON_COMPANY, fetch_context
    )

    assert result.complete is True
    assert result.jobs[0].posted_at == expected
    assert result.jobs[0].provenance["posted_at"] is FactSource.STRUCTURED_FEED


def test_amazon_invalid_posted_date_fails_closed_instead_of_inventing_today(
    fetch_context,
):
    row = _amazon_row(1)
    row["posted_date"] = "recently posted"

    result = AmazonFetcher(FakeHttp([{"hits": 1, "jobs": [row]}])).fetch(
        AMAZON_COMPANY, fetch_context
    )

    assert result.complete is False
    assert result.jobs == ()
    assert result.error == "Amazon posting schema mismatch"


def test_amazon_missing_posted_date_remains_unknown(fetch_context):
    row = _amazon_row(1)
    row.pop("posted_date")

    result = AmazonFetcher(FakeHttp([{"hits": 1, "jobs": [row]}])).fetch(
        AMAZON_COMPANY, fetch_context
    )

    assert result.complete is True
    assert result.jobs[0].posted_at is None
    assert "posted_at" not in result.jobs[0].provenance


def test_amazon_paginates_beyond_one_hundred(fetch_context):
    rows = [_amazon_row(index) for index in range(205)]
    http = FakeHttp(
        [
            {"hits": 205, "jobs": rows[:100]},
            {"hits": 205, "jobs": rows[100:200]},
            {"hits": 205, "jobs": rows[200:]},
        ]
    )

    result = AmazonFetcher(http).fetch(AMAZON_COMPANY, fetch_context)

    assert result.complete is True
    assert len(result.jobs) == 205
    assert [call.params["offset"] for call in http.calls] == [0, 100, 200]


def test_amazon_later_page_failure_is_partial_and_preserves_prior(fetch_context):
    rows = [_amazon_row(index) for index in range(101)]
    context = FetchContext(previous_active_ids=frozenset({"old"}))
    http = FakeHttp([{"hits": 101, "jobs": rows[:100]}, http_error(503)])
    result = AmazonFetcher(http).fetch(AMAZON_COMPANY, context)
    assert result.health is FetchHealth.PARTIAL
    assert result.active_ids == context.previous_active_ids


@pytest.mark.parametrize(
    "mutate",
    [
        lambda page: page.__setitem__("hits", "2"),
        lambda page: page["jobs"][1].__setitem__("id_icims", "us-job"),
        lambda page: page["jobs"][0].__setitem__("normalized_location", {}),
    ],
)
def test_amazon_malformed_inventory_never_completes(fetch_context, mutate):
    page = load_json_fixture("amazon_page.json")
    mutate(page)
    result = AmazonFetcher(FakeHttp([page])).fetch(AMAZON_COMPANY, fetch_context)
    assert result.complete is False
    assert result.health in {FetchHealth.PARTIAL, FetchHealth.FAILED}


def test_amazon_detail_uses_an_injected_clock(fetch_context):
    fixed_now = datetime(2026, 9, 7, 16, 0, tzinfo=timezone.utc)
    http = FakeHttp(
        [
            load_json_fixture("amazon_page.json"),
            TextResponse(load_text_fixture("amazon_detail.html"), 200, None, False),
        ]
    )
    fetcher = AmazonFetcher(http, now=lambda: fixed_now)
    job = next(
        value
        for value in fetcher.fetch(AMAZON_COMPANY, fetch_context).jobs
        if value.job_id == "us-job"
    )

    detail = fetcher.fetch_detail(AMAZON_COMPANY, job)

    assert detail.status is DetailStatus.HEALTHY
    assert detail.job.employment_type is EmploymentType.FULL_TIME
    assert detail.job.salary.annual_minimum == 110000
    assert detail.job.url == "https://www.amazon.jobs/en/jobs/us-job/apply"
    assert detail.job.provenance["description"] is FactSource.OFFICIAL_DETAIL


@pytest.mark.parametrize(
    ("status", "expected"),
    [(404, DetailStatus.CLOSED), (410, DetailStatus.CLOSED), (403, DetailStatus.FAILED)],
)
def test_amazon_detail_distinguishes_closure(make_job, status, expected):
    job = make_job(
        source_type="amazon", source_key="amazon:robotics-us", job_id="us-job"
    )
    detail = AmazonFetcher(FakeHttp([http_error(status)])).fetch_detail(
        AMAZON_COMPANY, job
    )
    assert detail.status is expected


@pytest.mark.parametrize("change", ["expired", "disabled", "identifier", "malformed"])
def test_amazon_inactive_or_unverified_html_fails(make_job, change):
    html = load_text_fixture("amazon_detail.html")
    if change == "expired":
        html = html.replace("2026-10-05T23:59:59Z", "2026-01-01T00:00:00Z")
    elif change == "disabled":
        html = html.replace("<a href=", '<a aria-disabled="true" href=')
    elif change == "identifier":
        html = html.replace('"value":"us-job"', '"value":"wrong"')
    else:
        html = "<html><body>No structured posting</body></html>"
    fixed_now = datetime(2026, 9, 7, 16, 0, tzinfo=timezone.utc)
    job = make_job(
        source_type="amazon", source_key="amazon:robotics-us", job_id="us-job"
    )
    detail = AmazonFetcher(
        FakeHttp([TextResponse(html, 200, None, False)]), now=lambda: fixed_now
    ).fetch_detail(AMAZON_COMPANY, job)
    assert detail.status is DetailStatus.FAILED


@pytest.mark.parametrize("status", [403, 429, 500])
def test_tesla_protected_responses_fail_closed(status):
    context = FetchContext(previous_active_ids=frozenset({"251107"}))
    result = TeslaFetcher(FakeHttp([http_error(status)])).fetch(TESLA_COMPANY, context)
    assert result.complete is False
    assert result.health is FetchHealth.FAILED
    assert result.active_ids == context.previous_active_ids
    assert result.jobs == ()
    assert result.error == f"Tesla official endpoint unavailable ({status})"


@pytest.mark.parametrize("payload", [{"unexpected": []}, "<html>blocked</html>", None, {}])
def test_tesla_unknown_200_schema_fails_closed(payload):
    context = FetchContext(previous_active_ids=frozenset({"251107"}))
    result = TeslaFetcher(FakeHttp([payload])).fetch(TESLA_COMPANY, context)
    assert result.health is FetchHealth.FAILED
    assert result.error == "Tesla official endpoint schema mismatch"
    assert result.active_ids == context.previous_active_ids
    assert result.jobs == ()
