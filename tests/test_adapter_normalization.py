from __future__ import annotations

from decimal import Decimal

import pytest

from src.config import load_config
from src.fetchers import get_fetcher, source_key
from src.fetchers.base import (
    normalize_employment_type,
    normalize_pay_period,
    normalize_workplace_type,
)
from src.models import EmploymentType, FactSource, FetchContext, PayPeriod, SalaryRange, WorkplaceType
from tests.fakes import FakeHttp, load_json_fixture


@pytest.mark.parametrize(
    "raw",
    ["FullTime", "FULL_TIME", "Full time", "SALARIED_FT"],
)
def test_adapter_full_time_variants_normalize_identically(raw):
    assert normalize_employment_type(raw) is EmploymentType.FULL_TIME


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("On-site", WorkplaceType.ONSITE),
        ("OnSite", WorkplaceType.ONSITE),
        ("hybrid", WorkplaceType.HYBRID),
        ("remote", WorkplaceType.REMOTE),
    ],
)
def test_workplace_variants_normalize(raw, expected):
    assert normalize_workplace_type(raw) is expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("1 YEAR", PayPeriod.YEAR), ("HOUR", PayPeriod.HOUR)],
)
def test_pay_period_variants_normalize(raw, expected):
    assert normalize_pay_period(raw) is expected


def test_missing_currency_is_never_assumed_usd():
    salary = SalaryRange(
        minimum=Decimal("120000"),
        period=PayPeriod.YEAR,
        source=FactSource.STRUCTURED_FEED,
    )
    assert salary.currency == ""
    assert salary.annual_minimum == Decimal("120000")


def test_all_configured_sources_have_exact_canonical_keys():
    settings = load_config("config.yaml")
    expected = load_json_fixture("source_roster.json")
    actual = [
        source_key(source.as_fetcher_mapping())
        for source in settings.companies
        if source.enabled
    ]
    assert actual == [row["source_key"] for row in expected]


@pytest.mark.parametrize(
    ("fetcher_name", "company", "responses"),
    [
        (
            "greenhouse",
            {"name": "Figure", "fetcher": "greenhouse", "slug": "figureai"},
            ("greenhouse_jobs.json",),
        ),
        (
            "ashby",
            {"name": "Applied Intuition", "fetcher": "ashby", "slug": "applied"},
            ("ashby_jobs.json",),
        ),
        (
            "lever",
            {"name": "Zoox", "fetcher": "lever", "slug": "zoox"},
            ("lever_jobs_page_1.json",),
        ),
        (
            "gem",
            {"name": "Chef Robotics", "fetcher": "gem", "slug": "chef-robotics"},
            ("gem_jobs.json",),
        ),
        (
            "rippling",
            {"name": "Foundation Robotics", "fetcher": "rippling", "board": "foundation-robotics"},
            ("rippling_jobs.json",),
        ),
        (
            "amazon",
            {"name": "Amazon Robotics", "fetcher": "amazon", "source_id": "robotics-us", "search_query": "robotics"},
            ("amazon_page.json",),
        ),
    ],
)
def test_successful_list_adapters_stamp_source_identity(fetcher_name, company, responses):
    http = FakeHttp([load_json_fixture(name) for name in responses])
    result = get_fetcher(fetcher_name, http=http).fetch(company, FetchContext())
    assert result.jobs
    assert all(job.source_type == fetcher_name for job in result.jobs)
    assert all(job.source_key == source_key(company) for job in result.jobs)
