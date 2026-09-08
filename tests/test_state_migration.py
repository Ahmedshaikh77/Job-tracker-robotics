import json
from pathlib import Path

import pytest

from src.state import StateCorruptionError, StateManager


FIXTURE = Path(__file__).parent / "fixtures" / "state_v1.json"


def test_v1_migration_keeps_only_alerted_identity_and_requires_save(tmp_path):
    path = tmp_path / "state.json"
    path.write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")

    manager = StateManager.load(path)

    assert manager.state["schema_version"] == 2
    assert len(manager.state["candidates"]) == 1
    assert len(manager.state["delivery"]["delivered"]) == 1
    candidate = next(iter(manager.state["candidates"].values()))
    assert candidate["migration_baseline_pending"] is True
    assert candidate["migration_snapshot"] == {
        "company": "figure",
        "title": "robotics test engineer",
        "location": "sunnyvale ca",
        "url": "https://example.test/jobs/123",
    }
    assert manager.state["sources"] == {}
    assert manager.dirty is True
    assert manager.is_persisted() is False

    manager.save_atomic()
    assert json.loads(path.read_text(encoding="utf-8"))["schema_version"] == 2
    assert manager.is_persisted() is True


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"random": {}},
        {"seen_jobs": []},
        {"seen_jobs": {}, "company_failures": []},
        {"seen_jobs": {}, "extra": {}},
    ],
)
def test_schemaless_or_malformed_v1_is_corruption(tmp_path, payload):
    path = tmp_path / "state.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(StateCorruptionError):
        StateManager.load(path)


def test_missing_file_is_dirty_unpersisted_v2(tmp_path):
    manager = StateManager.load(tmp_path / "missing.json")
    assert manager.state["schema_version"] == 2
    assert manager.dirty is True
    assert manager.persisted_fingerprint() == "missing"
    assert manager.is_persisted() is False
