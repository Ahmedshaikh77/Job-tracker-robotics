"""Immediate Moderate delivery through real queues, receipts, and state deltas."""
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
import json
from pathlib import Path

from src.alerts import TelegramTransientError
from src.config import load_config
from src.digest import prepare_health_summary, run_moderate_digest
from src.state import StateManager
from src.state_merge import StateDelta, apply_delta
from tests.test_roundup import roundup_harness


CONFIG_PATH = Path(__file__).parents[1] / "config.yaml"


def _queued_before_switch(harness):
    harness.add_job("baseline")
    harness.seed()
    job = harness.add_job("new")
    harness.scores[harness.identity(job)] = 74
    harness.clock[0] += timedelta(minutes=15)
    report = harness.tracker.run("live", phase="prepare", run_id="old-run")
    assert report.queued_moderate == 1
    assert harness.messages == []
    item, = StateManager.load(harness.path).pending_moderate()
    harness.tracker.settings = replace(
        harness.tracker.settings, digest=load_config(CONFIG_PATH).digest
    )
    return item


def test_production_policy_releases_existing_moderate_before_evening_and_persists_receipt(roundup_harness):
    harness = roundup_harness
    item = _queued_before_switch(harness)
    before = deepcopy(StateManager.load(harness.path).state)
    harness.tracker.delta_path = harness.path.with_name("delivery.delta.json")
    report = harness.tracker.run("live", phase="deliver", run_id="new-run")
    assert report.exit_code == 0
    assert report.delivered == 1
    after = StateManager.load(harness.path).state
    receipt = after["delivery"]["delivered"][item.revision_id]
    assert receipt["queued_run_id"] == "old-run"
    assert receipt["message_id"] == 1
    assert not after["digest"]["pending_moderate"]
    assert after["digest"]["last_processed_date"] is None
    assert item.application_url in harness.messages[0]
    assert "Daily moderate matches" not in harness.messages[0]
    delta = StateDelta.from_dict(json.loads(harness.tracker.delta_path.read_text()))
    assert apply_delta(before, delta) == after
    assert apply_delta(after, delta) == after
    replay = harness.tracker.run("live", phase="deliver", run_id="new-run")
    assert replay.delivered == 0
    assert len(harness.messages) == 1


def test_later_moderate_same_day_sends_despite_existing_digest_completion(roundup_harness):
    harness = roundup_harness
    _queued_before_switch(harness)
    manager = StateManager.load(harness.path)
    manager.complete_digest("2026-09-08", harness.clock[0].isoformat())
    manager.save_atomic()
    assert harness.tracker.run("live", phase="deliver", run_id="first").delivered == 1
    job = harness.add_job("later")
    harness.scores[harness.identity(job)] = 70
    harness.clock[0] += timedelta(minutes=15)
    prepared = harness.tracker.run("live", phase="prepare", run_id="second")
    assert prepared.queued_moderate == 1
    assert len(harness.messages) == 1
    delivered = harness.tracker.run("live", phase="deliver", run_id="second")
    assert delivered.delivered == 1
    assert len(harness.messages) == 2
    assert len(StateManager.load(harness.path).state["delivery"]["delivered"]) == 2


def test_immediate_moderate_send_failure_leaves_original_queue_and_no_receipt(roundup_harness):
    harness = roundup_harness
    item = _queued_before_switch(harness)

    class Unavailable:
        def send_message(self, text):
            raise TelegramTransientError("controlled outage")

    harness.tracker.notifier_factory = Unavailable
    report = harness.tracker.run("live", phase="deliver", run_id="failure")
    assert report.exit_code == 1
    assert report.delivered == 0
    stored = StateManager.load(harness.path)
    assert [pending.revision_id for pending in stored.pending_moderate()] == [item.revision_id]
    assert stored.state["delivery"]["delivered"] == {}


def test_immediate_moderate_dirty_state_does_not_send(roundup_harness):
    harness = roundup_harness
    _queued_before_switch(harness)
    manager = StateManager.load(harness.path)
    manager.dirty = True
    report = run_moderate_digest(manager, harness.tracker.notifier_factory(), harness.clock[0],
                                 policy=harness.tracker.settings.digest)
    assert report.failed
    assert harness.messages == []


def test_immediate_moderate_preserves_daily_health_schedule_and_filters(roundup_harness):
    harness = roundup_harness
    _queued_before_switch(harness)
    manager = StateManager.load(harness.path)
    assert prepare_health_summary(manager, harness.tracker.settings, harness.clock[0]) is None
    assert manager.state["digest"]["last_health_summary_date"] is None
    harness.ineligible.add((harness.keys[0], "new"))
    harness.clock[0] += timedelta(minutes=15)
    assert harness.tracker.run("live", phase="prepare", run_id="recheck").exit_code == 0
    assert not StateManager.load(harness.path).pending_moderate()
    assert harness.tracker.run("live", phase="deliver", run_id="recheck").delivered == 0
    assert harness.messages == []


def test_disabling_immediate_mode_retains_evening_digest(roundup_harness):
    harness = roundup_harness
    _queued_before_switch(harness)
    harness.tracker.settings = replace(
        harness.tracker.settings,
        digest=replace(harness.tracker.settings.digest, moderate_immediate=False),
    )
    assert harness.tracker.run("live", phase="deliver", run_id="daytime").delivered == 0
    assert harness.messages == []
    harness.clock[0] = harness.clock[0].replace(hour=23, minute=30)
    assert harness.tracker.run("live", phase="deliver", run_id="evening").delivered == 1
    assert "Daily moderate matches" in harness.messages[0]
    assert StateManager.load(harness.path).state["digest"]["last_processed_date"] == "2026-09-08"
