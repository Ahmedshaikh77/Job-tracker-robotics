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
