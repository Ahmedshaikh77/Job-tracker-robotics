import json
from datetime import datetime, timezone

import pytest

from src.models import (
    AlertFact,
    AlertItem,
    EvidenceStatus,
    FactSource,
    PendingHealthSummary,
    QueueInvalidationReason,
    Recommendation,
)
from src.state import StateCorruptionError, StateManager


def fact(value="confirmed"):
    return AlertFact(value, EvidenceStatus.CONFIRMED, FactSource.OFFICIAL_DETAIL)


def alert(revision="candidate:0:abc", candidate="candidate", source="greenhouse:figureai", queued="2026-09-07T12:00:00+00:00"):
    return AlertItem(
        revision, candidate, 0, ("source:greenhouse:figureai:123",), source,
        "Figure", "Robotics Test Engineer", "https://example.test/jobs/123",
        fact("Sunnyvale, CA"), fact("onsite"), fact("2026-09-07"), queued,
        fact("$120,000"), fact("1 year"), fact("OPT compatible"), fact("yes"),
        90, Recommendation.STRONG, "Strong systems match", "None", "resume.pdf",
        "robotics_test_validation", "test resume", queued, "run-1", queued,
    )


def candidate_record(item):
    return {
        "first_seen_at": item.first_seen_at,
        "last_seen_at": item.first_seen_at,
        "last_verified_open_at": item.first_seen_at,
        "closed_at": None,
        "last_closed_at": None,
        "reopened_at": None,
        "reopen_generation": item.reopen_generation,
        "discovered_during_seed": False,
        "migration_baseline_pending": False,
        "migration_snapshot": None,
        "aliases": list(item.identity_aliases),
        "source_refs": {},
        "last_content_hash": "abc",
        "last_evaluation": {"eligible": True, "recommendation": item.recommendation.value},
        "seed_baseline": None,
        "last_alert_basis": None,
        "last_queued_revision_id": item.revision_id,
        "last_queue_invalidation": None,
        "record_updated_at": item.queued_at,
    }


def test_malformed_json_is_hard_failure(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("{broken", encoding="utf-8")
    with pytest.raises(StateCorruptionError, match="invalid JSON"):
        StateManager.load(path)
    assert path.read_text(encoding="utf-8") == "{broken"


def test_save_is_atomic_and_queue_round_trips(monkeypatch, tmp_path):
    manager = StateManager.load(tmp_path / "state.json")
    item = alert()
    manager.state["candidates"][item.candidate_id] = candidate_record(item)
    assert manager.queue_immediate(item)
    calls = []
    import src.state as state_module
    original = state_module.os.replace
    monkeypatch.setattr(state_module.os, "replace", lambda source, target: (calls.append((source, target)), original(source, target))[1])
    manager.save_atomic()
    assert len(calls) == 1
    loaded = StateManager.load(manager.path)
    assert loaded.pending_immediate() == (item,)
    stored = loaded.state["delivery"]["pending_immediate"][item.revision_id]
    assert stored["record_updated_at"] == item.queued_at


def test_queue_promotion_supersession_invalidation_and_delivery(tmp_path):
    manager = StateManager.load(tmp_path / "state.json")
    moderate = alert()
    manager.state["candidates"][moderate.candidate_id] = candidate_record(moderate)
    assert manager.queue_moderate(moderate)
    promoted_caller = alert(queued="2026-09-08T12:00:00+00:00")
    assert manager.queue_immediate(promoted_caller)
    assert manager.pending_moderate() == ()
    assert manager.pending_immediate()[0].queued_at == moderate.queued_at

    removed = manager.invalidate_candidate_queue(
        moderate.candidate_id,
        QueueInvalidationReason.ASSESSMENT_INELIGIBLE,
        datetime(2026, 9, 8, tzinfo=timezone.utc),
    )
    assert removed == (moderate.revision_id,)
    assert manager.invalidate_candidate_queue(
        moderate.candidate_id,
        QueueInvalidationReason.ASSESSMENT_INELIGIBLE,
        datetime(2026, 9, 8, tzinfo=timezone.utc),
    ) == ()
    assert manager.state["candidates"][moderate.candidate_id]["last_queued_revision_id"] is None

    manager.state["candidates"][moderate.candidate_id]["last_evaluation"] = {
        "eligible": True,
        "recommendation": Recommendation.STRONG.value,
    }
    assert manager.queue_immediate(moderate)


def test_health_summary_and_run_ledger_are_idempotent(tmp_path):
    manager = StateManager.load(tmp_path / "state.json")
    summary = PendingHealthSummary("health:2026-09-07", "2026-09-07", "healthy", "2026-09-07T12:00:00+00:00")
    assert manager.queue_health_summary(summary)
    assert not manager.queue_health_summary(summary)
    assert manager.pending_health_summaries() == (summary,)
    manager.mark_health_summary_delivered(summary.delivery_id, "2026-09-07T12:01:00+00:00", 42)
    assert manager.pending_health_summaries() == ()
    manager.record_run_eligibility("run", ["a"], "2026-09-07T12:00:00+00:00", "2026-09-07T11:59:00+00:00")
    manager.record_run_eligibility("run", ["b", "a"], "2026-09-07T12:05:00+00:00", "2026-09-07T12:06:00+00:00")
    assert manager.state["runs"]["run"]["eligible_immediate_revision_ids"] == ["a", "b"]


def test_completion_pairs_only_move_forward(tmp_path):
    manager = StateManager.load(tmp_path / "state.json")
    manager.complete_digest("2026-09-07", "2026-09-07T13:00:00+00:00")
    snapshot = json.dumps(manager.state, sort_keys=True)
    manager.dirty = False
    manager.complete_digest("2026-09-06", "2026-09-07T14:00:00+00:00")
    assert json.dumps(manager.state, sort_keys=True) == snapshot
    assert manager.dirty is False
