from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

import pytest

from src.state import StateLimits, empty_state, prune_state
from src.state_merge import DeltaMode, StateDelta, apply_delta, build_delta


NOW = datetime(2026, 9, 7, 20, tzinfo=timezone.utc)
FIXTURES = Path(__file__).parent / "fixtures"


def queue_item(revision="rev-1", candidate="candidate", source="greenhouse:figureai", at="2026-09-07T19:00:00Z"):
    return {
        "revision_id": revision,
        "candidate_id": candidate,
        "reopen_generation": 0,
        "identity_aliases": ["source:greenhouse:figureai:1"],
        "source_key": source,
        "queued_at": at,
        "record_updated_at": at,
    }


def candidate(at="2026-09-07T19:00:00Z"):
    return {
        "aliases": ["source:greenhouse:figureai:1"],
        "source_refs": {},
        "closed_at": None,
        "last_queue_invalidation": None,
        "record_updated_at": at,
    }


def receipt(at="2026-09-07T20:00:00Z"):
    return {
        "delivered_at": at,
        "candidate_id": "candidate",
        "reopen_generation": 0,
        "chunk_id": "chunk",
        "message_id": 1,
        "queued_run_id": "run-123",
        "fetch_completed_at": "2026-09-07T19:00:00Z",
        "identity_aliases": ["source:greenhouse:figureai:1"],
        "source_key": "greenhouse:figureai",
        "record_updated_at": at,
    }


def test_noop_fixture_round_trips_and_validates_has_changes():
    payload = json.loads((FIXTURES / "noop_state_delta_v2.json").read_text())
    delta = StateDelta.from_dict(payload)
    assert delta.to_dict() == payload
    assert delta.has_changes is False
    with pytest.raises(ValueError):
        StateDelta.from_dict({**payload, "has_changes": True})
    with pytest.raises(ValueError):
        StateDelta.from_dict({**payload, "unknown": {}})


def test_round_trip_and_replay_are_exact():
    base = empty_state()
    base["candidates"]["candidate"] = candidate()
    base["digest"]["pending_moderate"]["rev-1"] = queue_item()
    final = deepcopy(base)
    item = final["digest"]["pending_moderate"].pop("rev-1")
    final["delivery"]["pending_immediate"]["rev-1"] = item
    final["runs"]["run-123"] = {
        "eligible_immediate_revision_ids": ["rev-1"],
        "started_at": "2026-09-07T19:00:00Z",
        "fetch_completed_at": "2026-09-07T19:01:00Z",
        "record_updated_at": "2026-09-07T19:02:00Z",
    }
    final["digest"]["last_processed_date"] = "2026-09-07"
    final["digest"]["last_processed_at"] = "2026-09-07T20:00:00Z"

    delta = build_delta(base, final, "run-123", DeltaMode.LIVE_PREPARE, NOW)
    assert delta.pending_removals["moderate:rev-1"]["reason"] == "promoted"
    merged = apply_delta(base, delta)
    assert merged == final
    assert apply_delta(merged, delta) == final


def test_delivered_revision_cannot_be_resurrected_pending():
    base = empty_state()
    base["candidates"]["candidate"] = candidate()
    base["delivery"]["pending_immediate"]["rev-1"] = queue_item()
    final = deepcopy(base)
    final["delivery"]["pending_immediate"].pop("rev-1")
    final["delivery"]["delivered"]["rev-1"] = receipt()
    delta = build_delta(base, final, "run-123", DeltaMode.LIVE_DELIVER, NOW)

    remote = deepcopy(base)
    remote["digest"]["pending_moderate"]["rev-1"] = queue_item(at="2026-09-07T19:30:00Z")
    merged = apply_delta(remote, delta)
    assert "rev-1" in merged["delivery"]["delivered"]
    assert "rev-1" not in merged["delivery"]["pending_immediate"]
    assert "rev-1" not in merged["digest"]["pending_moderate"]


def test_invalidation_tombstone_allows_strictly_newer_requeue():
    base = empty_state()
    base["candidates"]["candidate"] = candidate()
    base["delivery"]["pending_immediate"]["rev-1"] = queue_item()
    final = deepcopy(base)
    final["delivery"]["pending_immediate"].pop("rev-1")
    final["candidates"]["candidate"]["last_queue_invalidation"] = {
        "revision_ids": ["rev-1"], "reason": "delivery-stale", "invalidated_at": "2026-09-07T20:00:00Z"
    }
    final["candidates"]["candidate"]["record_updated_at"] = "2026-09-07T20:00:00Z"
    delta = build_delta(base, final, "run-123", DeltaMode.LIVE_DELIVER, NOW)
    assert delta.pending_removals["immediate:rev-1"]["reason"] == "invalidated"
    remote = deepcopy(base)
    remote["delivery"]["pending_immediate"]["rev-1"] = queue_item(at="2026-09-07T20:00:01Z")
    assert "rev-1" in apply_delta(remote, delta)["delivery"]["pending_immediate"]


def test_unexplained_pending_disappearance_is_rejected():
    base = empty_state()
    base["candidates"]["candidate"] = candidate()
    base["delivery"]["pending_immediate"]["rev-1"] = queue_item()
    final = deepcopy(base)
    final["delivery"]["pending_immediate"].clear()
    with pytest.raises(ValueError, match="unexplained"):
        build_delta(base, final, "run-123", DeltaMode.LIVE_PREPARE, NOW)


def test_real_cli_migrates_v1_and_replays_noop(tmp_path):
    source = tmp_path / "state-v1.json"
    first = tmp_path / "state-v2.json"
    second = tmp_path / "state-v2-replayed.json"
    source.write_text((FIXTURES / "state_v1.json").read_text(), encoding="utf-8")
    delta = FIXTURES / "noop_state_delta_v2.json"
    for input_path, output_path in ((source, first), (first, second)):
        completed = subprocess.run(
            [sys.executable, "-m", "src.state_merge", "--state", str(input_path), "--delta", str(delta), "--output", str(output_path)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr
    assert json.loads(first.read_text())["schema_version"] == 2
    assert first.read_bytes() == second.read_bytes()


def test_all_retention_tombstones_round_trip_exactly():
    old = "2025-01-01T00:00:00Z"
    base = empty_state()
    expired_candidate = candidate(old)
    expired_candidate["closed_at"] = old
    base["candidates"]["expired"] = expired_candidate
    base["runs"]["old-run"] = {
        "eligible_immediate_revision_ids": [],
        "started_at": old,
        "fetch_completed_at": old,
        "record_updated_at": old,
    }
    base["delivery"]["delivered"]["old-receipt"] = receipt(old)
    base["delivery"]["pending_immediate"]["old-pending"] = queue_item(
        revision="old-pending", at=old
    )
    base["digest"]["pending_health_summaries"]["old-health"] = {
        "delivery_id": "old-health", "local_date": "2025-01-01", "text": "old",
        "queued_at": old, "record_updated_at": old,
    }
    base["digest"]["delivered_health_summaries"]["old-health-receipt"] = {
        "local_date": "2025-01-01", "delivered_at": old,
        "message_id": 1, "record_updated_at": old,
    }
    final = deepcopy(base)
    prune_state(final, NOW, StateLimits())
    delta = build_delta(base, final, "run-123", DeltaMode.LIVE_PREPARE, NOW)
    assert set(delta.candidate_removals) == {"expired"}
    assert set(delta.run_removals) == {"old-run"}
    assert set(delta.delivered_removals) == {"old-receipt"}
    assert set(delta.pending_health_removals) == {"old-health"}
    assert set(delta.delivered_health_removals) == {"old-health-receipt"}
    assert delta.pending_removals["immediate:old-pending"]["reason"] == "expired"
    assert apply_delta(base, delta) == final


def test_custom_limits_drive_delta_retention_and_replay():
    old = "2026-09-01T00:00:00Z"
    limits = StateLimits(pending_max_age_days=5)
    base = empty_state()
    base["candidates"]["candidate"] = candidate(old)
    base["delivery"]["pending_immediate"]["old-pending"] = queue_item(
        revision="old-pending", at=old
    )
    final = deepcopy(base)
    prune_state(final, NOW, limits)

    delta = build_delta(
        base,
        final,
        "run-custom-limits",
        DeltaMode.LIVE_PREPARE,
        NOW,
        limits=limits,
    )

    assert delta.pending_removals["immediate:old-pending"]["reason"] == "expired"
    assert apply_delta(base, delta, limits=limits) == final


def test_apply_delta_preserves_records_retained_by_custom_limits():
    old = "2026-08-01T00:00:00Z"
    limits = StateLimits(pending_max_age_days=60)
    state = empty_state()
    state["candidates"]["candidate"] = candidate(old)
    state["delivery"]["pending_immediate"]["retained"] = queue_item(
        revision="retained", at=old
    )
    delta = build_delta(
        state,
        state,
        "run-custom-limits",
        DeltaMode.LIVE_PREPARE,
        NOW,
        limits=limits,
    )

    assert "retained" in apply_delta(state, delta, limits=limits)["delivery"][
        "pending_immediate"
    ]


def test_run_ids_union_and_newer_remote_source_inventory_wins():
    base = empty_state()
    base["sources"]["source"] = {
        "health": "healthy", "circuit": "closed", "active_ids": ["1"],
        "etag": "base", "fingerprint": "base", "source_total": 1,
        "total_is_authoritative": True,
        "last_complete_at": "2026-09-07T18:00:00Z",
        "record_updated_at": "2026-09-07T18:00:00Z",
    }
    base["runs"]["run"] = {
        "eligible_immediate_revision_ids": ["a"],
        "started_at": "2026-09-07T18:00:00Z",
        "fetch_completed_at": "2026-09-07T18:01:00Z",
        "record_updated_at": "2026-09-07T18:02:00Z",
    }
    final = deepcopy(base)
    final["sources"]["source"]["health"] = "partial"
    final["sources"]["source"]["record_updated_at"] = "2026-09-07T19:00:00Z"
    final["runs"]["run"]["eligible_immediate_revision_ids"] = ["a", "b"]
    final["runs"]["run"]["record_updated_at"] = "2026-09-07T19:00:00Z"
    delta = build_delta(base, final, "run-123", DeltaMode.LIVE_PREPARE, NOW)

    remote = deepcopy(base)
    remote["sources"]["source"].update({
        "active_ids": ["1", "2"], "etag": "remote", "source_total": 2,
        "last_complete_at": "2026-09-07T19:30:00Z",
    })
    remote["runs"]["run"]["eligible_immediate_revision_ids"] = ["a", "c"]
    merged = apply_delta(remote, delta)
    assert merged["sources"]["source"]["health"] == "partial"
    assert merged["sources"]["source"]["active_ids"] == ["1", "2"]
    assert merged["sources"]["source"]["etag"] == "remote"
    assert merged["runs"]["run"]["eligible_immediate_revision_ids"] == ["a", "b", "c"]


def test_cli_uses_configured_state_limits(tmp_path):
    old = "2026-08-01T00:00:00Z"
    state = empty_state()
    state["candidates"]["candidate"] = candidate(old)
    state["delivery"]["pending_immediate"]["retained"] = queue_item(
        revision="retained", at=old
    )
    state_path = tmp_path / "state.json"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        Path("config.yaml")
        .read_text(encoding="utf-8")
        .replace("pending_max_age_days: 30", "pending_max_age_days: 60"),
        encoding="utf-8",
    )
    output_path = tmp_path / "merged.json"

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "src.state_merge",
            "--state",
            str(state_path),
            "--delta",
            str(FIXTURES / "noop_state_delta_v2.json"),
            "--output",
            str(output_path),
            "--config",
            str(config_path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "retained" in json.loads(output_path.read_text())["delivery"][
        "pending_immediate"
    ]
