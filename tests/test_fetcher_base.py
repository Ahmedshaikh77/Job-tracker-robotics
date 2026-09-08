from __future__ import annotations

import pytest

from src.fetchers.base import Fetcher, source_key
from src.models import DetailStatus


class ConcreteFetcher(Fetcher):
    name = "test"

    def fetch(self, company, context):
        raise NotImplementedError


def test_abstract_fetcher_cannot_be_instantiated():
    with pytest.raises(TypeError):
        Fetcher()


def test_default_detail_cannot_verify_job(make_job):
    detail = ConcreteFetcher().fetch_detail({}, make_job())

    assert detail.status is DetailStatus.FAILED
    assert detail.job is None


@pytest.mark.parametrize(
    ("company", "expected"),
    [
        ({"fetcher": "greenhouse", "slug": "figureai"}, "greenhouse:figureai"),
        ({"fetcher": "rippling", "board": "foundation"}, "rippling:foundation"),
        ({"fetcher": "amazon", "source_id": "robotics-us"}, "amazon:robotics-us"),
        ({"fetcher": "tesla", "board": "careers"}, "tesla:careers"),
        (
            {
                "fetcher": "workday",
                "host": "example.wd1.myworkdayjobs.com",
                "tenant": "example",
                "site": "Careers",
            },
            "workday:example.wd1.myworkdayjobs.com:example:Careers",
        ),
    ],
)
def test_source_key_uses_adapter_specific_identity(company, expected):
    assert source_key(company) == expected


def test_source_key_rejects_missing_identity():
    with pytest.raises((KeyError, ValueError)):
        source_key({"fetcher": "greenhouse", "name": "Figure"})
