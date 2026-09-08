from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from src.alerts import TelegramReceipt
from src.config import (
    AppSettings,
    DigestPolicy,
    FetchPolicy,
    MatchingPolicy,
    SourceConfig,
    StatePolicy,
    TelegramPolicy,
)
from src.fetchers.base import source_key
from src.models import (
    CircuitState,
    DetailResult,
    DetailStatus,
    FetchContext,
    FetchHealth,
    FetchResult,
    Recommendation,
    ScoreBreakdown,
)
from src.orchestrator import JobTracker
from src.state import StateManager
from tests.test_source_lifecycle import assessment as base_assessment


NOW = datetime(2026, 9, 8, 14, tzinfo=timezone.utc)
ROUNDUP_LABEL = "Current openings roundup (not necessarily newly posted)."


def _breakdown(score):
    remaining = score
    values = []
    for maximum in (25, 15, 15, 10, 10, 10, 10, 5):
        value = min(remaining, maximum)
        values.append(value)
        remaining -= value
    assert remaining == 0
    return ScoreBreakdown(1, *values)


@pytest.fixture
def roundup_harness(tmp_path, make_job, monkeypatch):
    sources = (
        SourceConfig(
            name="Alpha Robotics",
            fetcher="greenhouse",
            slug="alpha",
            required_for_validation=True,
        ),
        SourceConfig(
            name="Beta Robotics",
            fetcher="greenhouse",
            slug="beta",
            required_for_validation=True,
        ),
    )
    keys = tuple(source_key(item.as_fetcher_mapping()) for item in sources)
    settings = AppSettings(
        FetchPolicy(),
        StatePolicy(),
        MatchingPolicy(),
        DigestPolicy(),
        TelegramPolicy(minimum_send_interval_seconds=0.001),
        sources,
        4,
    )
    jobs = {key: [] for key in keys}
    scores = {}
    recommendations = {}
    ineligible = set()
    roundup_only_eligible = set()
    closed_details = set()
    failed_details = set()
    failed_sources = set()
    contexts = []
    detail_calls = []
    evaluation_roundup_flags = []
    messages = []
    clock = [NOW]
    path = tmp_path / "state.json"

    def add_job(index, *, source_index=0, url=None):
        key = keys[source_index]
        job_id = str(index)
        job = make_job(
            source_type="greenhouse",
            source_key=key,
            company=sources[source_index].name,
            job_id=job_id,
            url=url or f"https://example.test/{sources[source_index].slug}/{job_id}",
        )
        jobs[key].append(job)
        return job

    def identity(job):
        return (job.source_key, job.job_id)

    class Feed:
        def fetch(self, company, context):
            key = source_key(company)
            contexts.append((key, context))
            if key in failed_sources:
                return FetchResult(
                    jobs=(),
                    active_ids=frozenset(),
                    complete=False,
                    source_total=None,
                    pages_fetched=0,
                    fetched_at=clock[0].isoformat(),
                    health=FetchHealth.FAILED,
                    error="controlled-source-failure",
                )
            found = tuple(jobs[key])
            if not found:
                return FetchResult(
                    jobs=(),
                    active_ids=frozenset(),
                    complete=True,
                    source_total=0,
                    pages_fetched=1,
                    fetched_at=clock[0].isoformat(),
                    health=FetchHealth.EMPTY_VALID,
                    total_is_authoritative=True,
                    etag="empty-etag",
                    fingerprint="empty-fingerprint",
                )
            return FetchResult(
                jobs=found,
                active_ids=frozenset(job.job_id for job in found),
                complete=True,
                source_total=len(found),
                pages_fetched=1,
                fetched_at=clock[0].isoformat(),
                health=FetchHealth.HEALTHY,
                total_is_authoritative=True,
                etag="inventory-etag",
                fingerprint="inventory-fingerprint",
            )

        def fetch_detail(self, company, job):
            key = identity(job)
            detail_calls.append(key)
            if key in closed_details:
                return DetailResult(None, DetailStatus.CLOSED, clock[0].isoformat())
            if key in failed_details:
                return DetailResult(
                    None,
                    DetailStatus.FAILED,
                    clock[0].isoformat(),
                    error="controlled-detail-failure",
                )
            return DetailResult(job, DetailStatus.HEALTHY, clock[0].isoformat())

    class Notifier:
        def validate_credentials(self):
            return None

        def send_message(self, text):
            messages.append(text)
            return TelegramReceipt(len(messages), clock[0].isoformat())

    def evaluate(job, candidate_id, candidate, profile, now, **kwargs):
        del profile, now
        key = identity(job)
        evaluation_roundup_flags.append(kwargs.get("current_roundup"))
        recommendation = recommendations.get(key, Recommendation.MODERATE)
        score = scores.get(key, 80)
        current = base_assessment(
            job,
            candidate_id,
            candidate["reopen_generation"],
            recommendation=recommendation,
        )
        current = replace(
            current,
            score=score,
            score_breakdown=_breakdown(score),
            match_reason=f"controlled match {job.job_id}",
        )
        if key in ineligible or (
            key in roundup_only_eligible and not kwargs.get("current_roundup")
        ):
            current = replace(
                current,
                eligible=False,
                recommendation=Recommendation.SKIP,
                hard_blocks=("controlled hard block",),
            )
        return current

    monkeypatch.setattr("src.evaluation.evaluate_job", evaluate)
    tracker = JobTracker(
        settings=settings,
        profile=None,
        state_path=path,
        environ={
            "TELEGRAM_BOT_TOKEN": "test-token",
            "TELEGRAM_CHAT_ID": "test-chat",
            "JOB_TRACKER_LIVE_ENABLED": "true",
        },
        now=lambda: clock[0],
        fetcher_factory=lambda name: Feed(),
        notifier_factory=Notifier,
    )

    def seed(run_id="seed-1"):
        report = tracker.run("seed", run_id=run_id)
        assert report.exit_code == 0
        return StateManager.load(path)

    return SimpleNamespace(
        tracker=tracker,
        sources=sources,
        keys=keys,
        jobs=jobs,
        scores=scores,
        recommendations=recommendations,
        ineligible=ineligible,
        roundup_only_eligible=roundup_only_eligible,
        closed_details=closed_details,
        failed_details=failed_details,
        failed_sources=failed_sources,
        contexts=contexts,
        detail_calls=detail_calls,
        evaluation_roundup_flags=evaluation_roundup_flags,
        messages=messages,
        clock=clock,
        path=path,
        add_job=add_job,
        identity=identity,
        seed=seed,
    )


@pytest.mark.parametrize(
    ("mode", "phase", "event"),
    (
        ("dry-run", None, "schedule"),
        ("live", "prepare", "schedule"),
        ("live", "deliver", "manual"),
        ("seed", None, "manual"),
        ("validate-only", None, "workflow_dispatch"),
    ),
)
def test_current_roundup_rejects_unauthorized_run_shapes_before_state_read(
    tmp_path, mode, phase, event
):
    settings = SimpleNamespace(telegram=None, companies=())
    tracker = JobTracker(
        settings=settings,
        profile=None,
        state_path=tmp_path / "state.json",
        environ={
            "TELEGRAM_BOT_TOKEN": "test-token",
            "TELEGRAM_CHAT_ID": "test-chat",
            "JOB_TRACKER_LIVE_ENABLED": "true",
        },
    )
    tracker._load_state = lambda: (_ for _ in ()).throw(AssertionError("state read"))

    report = tracker.run(
        mode,
        phase=phase,
        event=event,
        run_id="roundup-run" if mode == "live" else None,
        current_roundup=True,
    )

    assert report.exit_code == 2
    assert report.delivery_error == "invalid-current-roundup"


def test_current_roundup_forces_full_inventory_and_official_detail_checks(
    roundup_harness,
):
    harness = roundup_harness
    jobs = [harness.add_job("alpha-one"), harness.add_job("beta-one", source_index=1)]
    harness.seed()
    harness.contexts.clear()
    harness.detail_calls.clear()
    harness.evaluation_roundup_flags.clear()

    report = harness.tracker.run("dry-run", current_roundup=True)

    assert report.exit_code == 0
    assert [context for _, context in harness.contexts] == [
        FetchContext(force_full=True),
        FetchContext(force_full=True),
    ]
    assert set(harness.detail_calls) == {harness.identity(job) for job in jobs}
    assert harness.evaluation_roundup_flags == [True, True]


def test_current_roundup_respects_an_open_source_circuit(roundup_harness):
    harness = roundup_harness
    job = harness.add_job("blocked-by-circuit")
    manager = harness.seed()
    source = manager.state["sources"][job.source_key]
    source["circuit"] = CircuitState.OPEN.value
    source["next_probe_at"] = (NOW + timedelta(days=1)).isoformat()
    manager.dirty = True
    manager.save_atomic()
    before = harness.path.read_bytes()
    harness.contexts.clear()
    harness.detail_calls.clear()

    report = harness.tracker.run("dry-run", current_roundup=True)

    assert report.exit_code == 0
    assert all(key != job.source_key for key, _ in harness.contexts)
    assert harness.identity(job) not in harness.detail_calls
    assert not report.preview_items
    assert harness.path.read_bytes() == before


def test_current_roundup_globally_selects_ten_and_queues_moderate_immediately(
    roundup_harness,
):
    harness = roundup_harness
    seeded = []
    for index in range(12):
        job = harness.add_job(f"job-{index:02d}", source_index=index % 2)
        seeded.append(job)
        harness.scores[harness.identity(job)] = 70 + index
        harness.recommendations[harness.identity(job)] = Recommendation.MODERATE
    manager = harness.seed()

    report = harness.tracker.run(
        "live",
        phase="prepare",
        event="workflow_dispatch",
        run_id="roundup-live-1",
        current_roundup=True,
    )

    assert report.exit_code == 0
    assert report.queued_immediate == 10
    assert report.queued_moderate == 0
    manager = StateManager.load(harness.path)
    selected = manager.pending_immediate()
    assert len(selected) == 10
    expected_urls = {job.url for job in seeded[2:]}
    assert {item.application_url for item in selected} == expected_urls
    assert all(item.recommendation is Recommendation.MODERATE for item in selected)
    assert all(item.match_reason.startswith(ROUNDUP_LABEL) for item in selected)
    run = manager.state["runs"]["roundup-live-1"]
    assert set(run["eligible_immediate_revision_ids"]) == {
        item.revision_id for item in selected
    }
    for job in seeded[:2]:
        candidate_id = manager.candidate_id_for_source_ref(job.source_key, job.job_id)
        candidate = manager.candidate(candidate_id)
        assert candidate["last_alert_basis"] is None
        assert candidate["last_queued_revision_id"] is None


def test_current_roundup_requires_an_unalerted_complete_seed_baseline(
    roundup_harness,
):
    harness = roundup_harness
    labels = (
        "eligible",
        "missing-baseline",
        "already-alerted",
        "already-queued",
        "migration-pending",
        "legacy-delivered",
        "ineligible",
    )
    jobs = {label: harness.add_job(label) for label in labels}
    harness.roundup_only_eligible.add(harness.identity(jobs["legacy-delivered"]))
    manager = harness.seed()

    def record(label):
        job = jobs[label]
        candidate_id = manager.candidate_id_for_source_ref(job.source_key, job.job_id)
        return candidate_id, manager.state["candidates"][candidate_id]

    _, missing = record("missing-baseline")
    missing["seed_baseline"] = None
    _, alerted = record("already-alerted")
    alerted["last_alert_basis"] = deepcopy(alerted["seed_baseline"])
    harness.recommendations[harness.identity(jobs["already-alerted"])] = Recommendation.STRONG
    queued_id, queued = record("already-queued")
    queued_assessment = base_assessment(
        jobs["already-queued"],
        queued_id,
        queued["reopen_generation"],
        recommendation=Recommendation.MODERATE,
    )
    queued["last_queued_revision_id"] = queued_assessment.revision_id
    harness.recommendations[harness.identity(jobs["already-queued"])] = Recommendation.STRONG
    _, migration = record("migration-pending")
    migration["migration_baseline_pending"] = True
    migration["migration_snapshot"] = {
        "company": "alpha robotics",
        "title": "robotics test engineer",
        "location": "sunnyvale ca",
        "url": jobs["migration-pending"].url,
    }
    legacy_id, legacy = record("legacy-delivered")
    legacy_alias = "legacy-local:alpha robotics:legacy-delivered"
    legacy["aliases"].append(legacy_alias)
    stamp = NOW.isoformat()
    manager.state["delivery"]["delivered"]["legacy-revision"] = {
        "delivered_at": stamp,
        "candidate_id": legacy_id,
        "reopen_generation": 0,
        "chunk_id": "legacy-v1",
        "message_id": None,
        "queued_run_id": "legacy-v1",
        "fetch_completed_at": stamp,
        "identity_aliases": [legacy_alias],
        "source_key": None,
        "record_updated_at": stamp,
    }
    harness.ineligible.add(harness.identity(jobs["ineligible"]))
    manager.dirty = True
    manager.save_atomic()

    report = harness.tracker.run(
        "live",
        phase="prepare",
        run_id="roundup-gates",
        current_roundup=True,
    )

    assert report.queued_immediate == 1
    pending = StateManager.load(harness.path).pending_immediate()
    assert [item.application_url for item in pending] == [jobs["eligible"].url]


def test_current_roundup_deduplicates_cross_source_candidates_before_the_cap(
    roundup_harness,
):
    harness = roundup_harness
    shared_url = "https://example.test/shared-role"
    first = harness.add_job("shared-alpha", url=shared_url)
    duplicate = harness.add_job("shared-beta", source_index=1, url=shared_url)
    for job in (first, duplicate):
        harness.scores[harness.identity(job)] = 99
    for index in range(9):
        job = harness.add_job(f"unique-{index:02d}", source_index=index % 2)
        harness.scores[harness.identity(job)] = 80 - index
    harness.seed()

    report = harness.tracker.run(
        "live",
        phase="prepare",
        run_id="roundup-deduplicated",
        current_roundup=True,
    )

    assert report.queued_immediate == 10
    pending = StateManager.load(harness.path).pending_immediate()
    assert len(pending) == 10
    assert sum(item.application_url == shared_url for item in pending) == 1


def test_current_roundup_does_not_override_detail_or_source_failures(
    roundup_harness,
):
    harness = roundup_harness
    eligible = harness.add_job("eligible")
    closed = harness.add_job("closed")
    blocked = harness.add_job("ineligible")
    source_failed = harness.add_job("source-failed", source_index=1)
    harness.seed()
    harness.closed_details.add(harness.identity(closed))
    harness.ineligible.add(harness.identity(blocked))
    harness.failed_sources.add(source_failed.source_key)

    report = harness.tracker.run(
        "live",
        phase="prepare",
        run_id="roundup-failures",
        current_roundup=True,
    )

    assert report.exit_code == 1
    assert report.source_failures == (source_failed.source_key,)
    pending = StateManager.load(harness.path).pending_immediate()
    assert [item.application_url for item in pending] == [eligible.url]


def test_current_roundup_dry_run_previews_the_same_top_ten_without_writing(
    roundup_harness,
):
    harness = roundup_harness
    seeded = []
    for index in range(12):
        job = harness.add_job(f"preview-{index:02d}", source_index=index % 2)
        seeded.append(job)
        harness.scores[harness.identity(job)] = 70 + index
    harness.seed()
    before = harness.path.read_bytes()

    report = harness.tracker.run(
        "dry-run",
        event="workflow_dispatch",
        current_roundup=True,
    )

    assert report.exit_code == 0
    assert [item.score for item in report.preview_items] == list(range(81, 71, -1))
    assert {item.application_url for item in report.preview_items} == {
        job.url for job in seeded[2:]
    }
    assert all(item.match_reason.startswith(ROUNDUP_LABEL) for item in report.preview_items)
    assert harness.path.read_bytes() == before
    assert harness.messages == []


def test_unselected_new_candidate_remains_alertable_after_roundup_cap(
    roundup_harness,
):
    harness = roundup_harness
    harness.seed()
    added = []
    for index in range(11):
        job = harness.add_job(f"new-{index:02d}", source_index=index % 2)
        added.append(job)
        harness.scores[harness.identity(job)] = 70 + index
        harness.recommendations[harness.identity(job)] = Recommendation.STRONG

    roundup = harness.tracker.run(
        "live",
        phase="prepare",
        run_id="roundup-new",
        current_roundup=True,
    )
    assert roundup.queued_immediate == 10
    manager = StateManager.load(harness.path)
    lowest = added[0]
    lowest_id = manager.candidate_id_for_source_ref(lowest.source_key, lowest.job_id)
    assert manager.candidate(lowest_id)["last_alert_basis"] is None

    harness.clock[0] += timedelta(minutes=15)
    normal = harness.tracker.run(
        "live",
        phase="prepare",
        event="schedule",
        run_id="normal-after-roundup",
    )

    assert normal.queued_immediate == 1
    manager = StateManager.load(harness.path)
    assert {item.application_url for item in manager.pending_immediate()} == {
        job.url for job in added
    }


def test_current_roundup_promotes_a_reverified_pending_moderate_without_reattribution(
    roundup_harness,
):
    harness = roundup_harness
    harness.seed()
    job = harness.add_job("pending-moderate")

    normal = harness.tracker.run(
        "live",
        phase="prepare",
        event="schedule",
        run_id="original-moderate-run",
    )
    assert normal.queued_moderate == 1
    manager = StateManager.load(harness.path)
    original = manager.pending_moderate()[0]
    assert manager.pending_immediate() == ()

    before = harness.path.read_bytes()
    harness.detail_calls.clear()
    preview = harness.tracker.run("dry-run", current_roundup=True)
    assert [item.revision_id for item in preview.preview_items] == [original.revision_id]
    assert harness.identity(job) in harness.detail_calls
    assert harness.path.read_bytes() == before

    promoted = harness.tracker.run(
        "live",
        phase="prepare",
        run_id="roundup-promotion-run",
        current_roundup=True,
    )

    assert promoted.queued_immediate == 1
    manager = StateManager.load(harness.path)
    assert manager.pending_moderate() == ()
    assert len(manager.pending_immediate()) == 1
    item = manager.pending_immediate()[0]
    assert item.revision_id == original.revision_id
    assert item.queued_run_id == "original-moderate-run"
    assert "roundup-promotion-run" not in manager.state["runs"]


def test_roundup_delivery_uses_a_generic_verified_matches_heading(roundup_harness):
    harness = roundup_harness
    harness.add_job("heading")
    harness.seed()
    prepared = harness.tracker.run(
        "live",
        phase="prepare",
        run_id="roundup-heading",
        current_roundup=True,
    )
    assert prepared.queued_immediate == 1

    delivered = harness.tracker.run(
        "live",
        phase="deliver",
        run_id="roundup-heading",
    )

    assert delivered.delivered == 1
    assert "Verified job matches" in harness.messages[0]
    assert "New strong matches" not in harness.messages[0]
