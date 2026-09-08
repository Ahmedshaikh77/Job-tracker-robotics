from pathlib import Path

from src.dedupe import candidate_aliases, normalize_official_url, resolve_candidate_id
from src.models import EmploymentType, Job
from src.state import StateManager


FIXTURE = Path(__file__).parent / "fixtures" / "state_v1.json"


def test_migrated_alerted_job_resolves_by_url_only_while_active(tmp_path):
    path = tmp_path / "state.json"
    path.write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
    manager = StateManager.load(path)
    candidate_id = next(iter(manager.state["candidates"]))
    job = Job(
        source_type="greenhouse",
        source_key="greenhouse:figureai",
        company="Figure",
        job_id="123",
        title="Robotics Test Engineer",
        location="Sunnyvale, CA",
        url="HTTPS://EXAMPLE.TEST/jobs/123/?utm_source=x#apply",
        employment_type=EmploymentType.FULL_TIME,
    )
    assert normalize_official_url(job.url) == "https://example.test/jobs/123"
    assert resolve_candidate_id(job, manager.state["candidates"]) == candidate_id
    aliases = candidate_aliases(job)
    assert "source:greenhouse:figureai:123" in aliases
