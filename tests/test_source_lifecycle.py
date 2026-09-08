import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from src.lifecycle import (
    RevisionPolicy,
    comparison_basis,
    is_migration_equivalent,
    max_delivered_generation,
    should_alert_revision,
)
from src.models import (
    AuthorizationAssessment,
    AuthorizationStatus,
    CompensationAssessment,
    CompensationStatus,
    DetailResult,
    DetailStatus,
    EmploymentType,
    ExperienceRequirement,
    FactSource,
    FetchHealth,
    FetchResult,
    FreshnessAssessment,
    FreshnessStatus,
    JobAssessment,
    QualificationAssessment,
    Recommendation,
    ScoreBreakdown,
)
from src.source_health import SourceHealthPolicy, classify_snapshot, should_probe
from src.state import StateManager


NOW = datetime(2026, 9, 7, 12, tzinfo=timezone.utc)
LATER = NOW + timedelta(minutes=30)
MUCH_LATER = LATER + timedelta(minutes=30)


def result(make_job, ids, *, health=FetchHealth.HEALTHY, complete=True, at=NOW, authoritative=True, unchanged=False, source="greenhouse:figureai"):
    active = frozenset(ids)
    jobs = () if unchanged or not complete else tuple(
        make_job(source_key=source, source_type=source.split(":", 1)[0], job_id=posting_id, url=f"https://example.test/jobs/{posting_id}")
        for posting_id in sorted(active)
    )
    source_total = len(active) if authoritative else None
    return FetchResult(
        jobs, active, complete, source_total, 1, at.isoformat(), health,
        authoritative, unchanged=unchanged,
    )


def assessment(job, candidate_id, generation=0, recommendation=Recommendation.STRONG, score=90):
    breakdown = ScoreBreakdown(1, 25, 15, 15, 10, 10, 10, 5, 0)
    assert breakdown.total == score
    return JobAssessment(
        job, candidate_id, generation, True, score, recommendation,
        "robotics_test_validation", "test match", "none", "resume.pdf", "best map",
        ExperienceRequirement(1, None, 1, None, None, None, False, "", False, True, "1 year", FactSource.OFFICIAL_DETAIL),
        AuthorizationAssessment(AuthorizationStatus.CONFIRMED_SUPPORT, "support", FactSource.OFFICIAL_DETAIL),
        FreshnessAssessment(FreshnessStatus.RECENT, True, 1, "recent", FactSource.OFFICIAL_DETAIL),
        CompensationAssessment(CompensationStatus.CONFIRMED_TARGET, "$100k+", 5, None, "pay", FactSource.OFFICIAL_DETAIL),
        QualificationAssessment((), (), (), (), 10),
        breakdown,
    )


def test_partial_does_not_close_and_two_complete_omissions_do(tmp_path, make_job):
    manager = StateManager.load(tmp_path / "state.json")
    job = make_job()
    candidate_id, _, _ = manager.observe_candidate(job, NOW, discovered_during_seed=False)
    manager.apply_fetch_result(job.source_key, result(make_job, {"123"}), NOW)
    manager.dirty = False
    manager.apply_fetch_result(job.source_key, result(make_job, set(), health=FetchHealth.PARTIAL, complete=False, authoritative=False), LATER)
    assert manager.candidate(candidate_id)["closed_at"] is None
    empty = result(make_job, set(), health=FetchHealth.EMPTY_VALID)
    manager.apply_fetch_result(job.source_key, empty, LATER)
    assert manager.candidate(candidate_id)["closed_at"] is None
    manager.apply_fetch_result(job.source_key, result(make_job, set(), health=FetchHealth.EMPTY_VALID, at=MUCH_LATER), MUCH_LATER)
    assert manager.candidate(candidate_id)["closed_at"] == MUCH_LATER.isoformat()


def test_three_failures_open_circuit_and_due_probe_closes_it(tmp_path, make_job):
    manager = StateManager.load(tmp_path / "state.json")
    for moment in (NOW, LATER, MUCH_LATER):
        manager.apply_fetch_result(
            "greenhouse:figureai",
            result(make_job, set(), health=FetchHealth.FAILED, complete=False, authoritative=False, at=moment),
            moment,
        )
    assert manager.source("greenhouse:figureai")["circuit"] == "open"
    assert manager.should_fetch("greenhouse:figureai", MUCH_LATER) is False
    probe_at = MUCH_LATER + timedelta(hours=24)
    assert manager.begin_fetch("greenhouse:figureai", probe_at) is True
    assert manager.source("greenhouse:figureai")["circuit"] == "half-open"
    manager.apply_fetch_result("greenhouse:figureai", result(make_job, {"123"}, at=probe_at), probe_at)
    assert manager.source("greenhouse:figureai")["circuit"] == "closed"


def test_shrink_is_partial_without_authoritative_override(tmp_path, make_job):
    manager = StateManager.load(tmp_path / "state.json")
    manager.apply_fetch_result("greenhouse:figureai", result(make_job, {str(i) for i in range(100)}), NOW)
    shrink = result(make_job, {str(i) for i in range(39)}, authoritative=False, at=LATER)
    transition = manager.apply_fetch_result("greenhouse:figureai", shrink, LATER)
    assert transition.health == FetchHealth.PARTIAL
    assert len(manager.source("greenhouse:figureai")["active_ids"]) == 100

    authoritative = result(make_job, {str(i) for i in range(39)}, at=MUCH_LATER)
    transition = manager.apply_fetch_result("greenhouse:figureai", authoritative, MUCH_LATER)
    assert transition.health == FetchHealth.HEALTHY
    assert len(manager.source("greenhouse:figureai")["active_ids"]) == 39


def test_detail_retry_identity_validation_and_clear(tmp_path, make_job):
    manager = StateManager.load(tmp_path / "state.json")
    job = make_job()
    candidate_id, _, _ = manager.observe_candidate(job, NOW, discovered_during_seed=False)
    before = json.dumps(manager.state, sort_keys=True)
    wrong = make_job(source_type="ashby", source_key="ashby:figure", job_id="999")
    with pytest.raises(ValueError):
        manager.apply_detail_result(candidate_id, job.source_key, job.job_id, DetailResult(wrong, DetailStatus.HEALTHY, NOW.isoformat()), NOW)
    assert json.dumps(manager.state, sort_keys=True) == before

    manager.apply_detail_result(candidate_id, job.source_key, job.job_id, DetailResult(None, DetailStatus.FAILED, NOW.isoformat()), NOW)
    assert manager.fetch_context(job.source_key, NOW).detail_retry_ids == frozenset({"123"})
    manager.apply_detail_result(candidate_id, job.source_key, job.job_id, DetailResult(job, DetailStatus.HEALTHY, LATER.isoformat()), LATER)
    assert manager.fetch_context(job.source_key, LATER).detail_retry_ids == frozenset()


def test_seed_baseline_and_revision_decisions(tmp_path, make_job):
    manager = StateManager.load(tmp_path / "state.json")
    job = make_job()
    candidate_id, previous, record = manager.observe_candidate(job, NOW, discovered_during_seed=True)
    assert previous is None
    current = assessment(job, candidate_id)
    manager.record_assessment(current, NOW.isoformat())
    assert comparison_basis(manager.candidate(candidate_id)) == manager.candidate(candidate_id)["seed_baseline"]
    assert not should_alert_revision(manager.candidate(candidate_id), current, LATER)

    changed = assessment(make_job(title="Hardware Validation Engineer"), candidate_id)
    assert should_alert_revision(manager.candidate(candidate_id), changed, LATER)


def test_record_alert_basis_clears_only_requeued_revision_marker(tmp_path, make_job):
    manager = StateManager.load(tmp_path / "state.json")
    job = make_job()
    candidate_id, _, _ = manager.observe_candidate(job, NOW, discovered_during_seed=False)
    item = assessment(job, candidate_id)
    record = manager.state["candidates"][candidate_id]
    record["last_queue_invalidation"] = {
        "revision_ids": [item.revision_id, "older"],
        "reason": "delivery-stale",
        "invalidated_at": NOW.isoformat(),
    }
    assert should_alert_revision(record, item, LATER)
    manager.record_alert_basis(item, LATER.isoformat())
    assert record["last_queue_invalidation"]["revision_ids"] == ["older"]
    assert record["last_queued_revision_id"] == item.revision_id


def test_source_seed_requires_accepted_complete_result(tmp_path, make_job):
    manager = StateManager.load(tmp_path / "state.json")
    with pytest.raises(ValueError):
        manager.mark_source_seeded("greenhouse:figureai", NOW.isoformat())
    manager.apply_fetch_result("greenhouse:figureai", result(make_job, {"123"}), NOW)
    assert manager.mark_source_seeded("greenhouse:figureai", NOW.isoformat()) is True
    assert manager.mark_source_seeded("greenhouse:figureai", LATER.isoformat()) is False


def test_helpers_are_policy_driven(make_job):
    policy = SourceHealthPolicy()
    assert should_probe({"circuit": "closed"}, NOW, policy)
    assert classify_snapshot(list(map(str, range(100))), result(make_job, {str(i) for i in range(39)}, authoritative=False), policy) is FetchHealth.PARTIAL
    assert max_delivered_generation({"source:a:1"}, {}) == -1
    migrated = {
        "migration_snapshot": {
            "company": "figure", "title": "robotics test engineer",
            "location": "sunnyvale ca", "url": "https://example.test/jobs/123",
        }
    }
    item = assessment(make_job(), "candidate")
    assert is_migration_equivalent(migrated, item)
    assert RevisionPolicy().compensation_threshold == Decimal("100000")
