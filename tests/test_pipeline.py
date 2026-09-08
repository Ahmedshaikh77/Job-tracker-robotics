"""Real-state integration of baseline, preparation, persistence, and delivery."""
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json

import pytest

from src.alerts import TelegramReceipt
from src.config import AppSettings, SourceConfig, FetchPolicy, StatePolicy, MatchingPolicy, DigestPolicy, TelegramPolicy
from src.models import FetchResult, FetchHealth, DetailResult, DetailStatus
from src.orchestrator import JobTracker
from src.state import StateManager
from src.state_merge import StateDelta, apply_delta
from tests.test_source_lifecycle import assessment


@pytest.fixture
def pipeline(tmp_path, make_job, monkeypatch):
    import src.evaluation
    monkeypatch.setattr(src.evaluation, 'evaluate_job',
        lambda job, cid, candidate, profile, now, **kw: assessment(job, cid, candidate['reopen_generation']))
    clock = [datetime(2026,9,7,18,tzinfo=timezone.utc)]
    jobs = [make_job(job_id='1',url='https://example.test/jobs/1')]
    failures = set()
    events = []
    source = SourceConfig(name='Figure',fetcher='greenhouse',slug='figureai',required_for_validation=True)
    settings = AppSettings(FetchPolicy(),StatePolicy(),MatchingPolicy(),DigestPolicy(),TelegramPolicy(),(source,),2)
    path = tmp_path/'state.json'

    class Feed:
        def fetch(self, company, context):
            events.append('fetch')
            return FetchResult(tuple(jobs),frozenset(j.job_id for j in jobs),True,len(jobs),1,
                               clock[0].isoformat(),FetchHealth.HEALTHY,True)
        def fetch_detail(self, company, job):
            events.append('detail:'+job.job_id)
            if job.job_id in failures:
                return DetailResult(None,DetailStatus.CLOSED,clock[0].isoformat())
            return DetailResult(job,DetailStatus.HEALTHY,clock[0].isoformat())

    class Notifier:
        def send_message(self, text):
            stored = json.loads(path.read_text())
            assert stored['delivery']['pending_immediate']
            events.append('send')
            return TelegramReceipt(1,clock[0].isoformat())

    tracker = JobTracker(settings=settings,profile=None,state_path=path,
        environ={'TELEGRAM_BOT_TOKEN':'token','TELEGRAM_CHAT_ID':'chat','JOB_TRACKER_LIVE_ENABLED':'true'},
        now=lambda:clock[0],fetcher_factory=lambda name:Feed(),notifier_factory=Notifier)
    return tracker,jobs,failures,events,clock,path


def test_seed_new_job_prepare_deliver_no_duplicates(pipeline, make_job):
    tracker,jobs,failures,events,clock,path = pipeline
    seed = tracker.run('seed',run_id='seed-1')
    assert seed.exit_code == 0
    assert 'send' not in events
    assert not StateManager.load(path).pending_immediate()
    jobs.append(make_job(job_id='2',url='https://example.test/jobs/2'))
    clock[0] += timedelta(minutes=30)
    report = tracker.run('live',phase='prepare',run_id='live-1')
    assert report.exit_code == 0
    assert report.queued_immediate == 1
    assert 'send' not in events
    state = StateManager.load(path)
    assert len(state.pending_immediate()) == 1
    assert state.pending_immediate()[0].queued_run_id == 'live-1'
    report = tracker.run('live',phase='deliver',run_id='live-1')
    assert report.exit_code == 0
    assert report.delivered == 1
    assert report.same_run_delivery_rate == 1
    baseline = path.read_bytes()
    clock[0] += timedelta(minutes=30)
    report = tracker.run('live',phase='prepare',run_id='live-2')
    assert report.queued_immediate == 0
    assert path.read_bytes() == baseline


def test_dry_run_never_changes_production_bytes(pipeline, make_job):
    tracker,jobs,failures,events,clock,path = pipeline
    assert tracker.run('seed',run_id='seed-1').exit_code == 0
    before = path.read_bytes()
    jobs.append(make_job(job_id='2',url='https://example.test/jobs/2'))
    report = tracker.run('dry-run')
    assert report.exit_code == 0
    assert report.preview_items
    assert path.read_bytes() == before
    assert 'send' not in events


def test_official_closure_removes_unsent_queue(pipeline, make_job):
    tracker,jobs,failures,events,clock,path = pipeline
    assert tracker.run('seed',run_id='seed-1').exit_code == 0
    jobs.append(make_job(job_id='2',url='https://example.test/jobs/2'))
    assert tracker.run('live',phase='prepare',run_id='live-1').queued_immediate == 1
    failures.add('2')
    clock[0] += timedelta(minutes=30)
    assert tracker.run('live',phase='prepare',run_id='live-2').exit_code == 0
    assert not StateManager.load(path).pending_immediate()
    assert tracker.run('live',phase='deliver',run_id='live-2').delivered == 0
    assert 'send' not in events


def test_prepare_and_receipt_deltas_roundtrip_real_pipeline(pipeline, make_job):
    tracker,jobs,failures,events,clock,path = pipeline
    tracker.delta_path = path.with_name('delta.json')
    base = deepcopy(StateManager.load(path).state)
    report = tracker.run('seed',run_id='seed-1')
    assert report.exit_code == 0 and report.delta_ready
    delta = StateDelta.from_dict(json.loads(tracker.delta_path.read_text()))
    assert apply_delta(base,delta) == StateManager.load(path).state
    jobs.append(make_job(job_id='2',url='https://example.test/jobs/2'))
    clock[0] += timedelta(minutes=30)
    base = deepcopy(StateManager.load(path).state)
    report = tracker.run('live',phase='prepare',run_id='live-1')
    assert report.exit_code == 0 and report.delta_ready
    delta = StateDelta.from_dict(json.loads(tracker.delta_path.read_text()))
    assert apply_delta(base,delta) == StateManager.load(path).state
    base = deepcopy(StateManager.load(path).state)
    report = tracker.run('live',phase='deliver',run_id='live-1')
    assert report.exit_code == 0 and report.delta_ready
    delta = StateDelta.from_dict(json.loads(tracker.delta_path.read_text()))
    assert apply_delta(base,delta) == StateManager.load(path).state


def test_recovery_is_exact_run_only_and_never_fetches(pipeline, make_job):
    tracker,jobs,failures,events,clock,path = pipeline
    assert tracker.run('seed',run_id='seed-1').exit_code == 0
    jobs.append(make_job(job_id='2',url='https://example.test/jobs/2'))
    assert tracker.run('live',phase='prepare',run_id='run-a').queued_immediate == 1
    jobs.append(make_job(job_id='3',url='https://example.test/jobs/3'))
    assert tracker.run('live',phase='prepare',run_id='run-b').queued_immediate == 1
    events.clear()
    tracker.environ.update(JOB_TRACKER_LIVE_ENABLED='false', JOB_TRACKER_RECOVERY_RUN_ID='run-a',
                            JOB_TRACKER_RECOVERY_CONFIRMATION='SEND PENDING')
    report = tracker.run('recover-delivery')
    assert report.exit_code == 0 and report.delivered == 1
    assert events == ['send']
    assert [item.queued_run_id for item in StateManager.load(path).pending_immediate()] == ['run-b']
