from datetime import datetime, timezone
from types import SimpleNamespace

from src.orchestrator import JobTracker, RunMode, RunPhase


def tracker(tmp_path, environ, notifier=None):
    settings = SimpleNamespace(telegram=None, companies=())
    return JobTracker(settings=settings, profile=None, state_path=tmp_path/'state.json',
                      environ=environ, notifier_factory=lambda: notifier,
                      now=lambda: datetime(2026,9,7,20,tzinfo=timezone.utc))


def test_inactive_live_never_touches_state_or_network(tmp_path):
    obj = tracker(tmp_path, {})
    obj._load_state = lambda: (_ for _ in ()).throw(AssertionError('state read'))
    report = obj.run(RunMode.LIVE, phase=RunPhase.PREPARE, event='schedule', run_id='run-1')
    assert report.exit_code == 0
    assert not report.delta_ready
    assert not (tmp_path/'state.json').exists()


def test_live_missing_credentials_fails_before_state_read(tmp_path):
    obj = tracker(tmp_path, {'JOB_TRACKER_LIVE_ENABLED': 'true'})
    obj._load_state = lambda: (_ for _ in ()).throw(AssertionError('state read'))
    report = obj.run(RunMode.LIVE, phase=RunPhase.PREPARE, run_id='run-1')
    assert report.exit_code == 2
    assert report.delivery_error == 'telegram-credentials-missing'


def test_smoke_is_isolated_exact_payload(tmp_path):
    messages = []
    notifier = SimpleNamespace(send_message=messages.append)
    payload = 'test " $HOME `touch nope`\n${{ inputs.value }}'
    obj = tracker(tmp_path, {'TELEGRAM_BOT_TOKEN':'token','TELEGRAM_CHAT_ID':'chat',
                            'JOB_TRACKER_SMOKE_PAYLOAD':payload}, notifier)
    obj._load_state = lambda: (_ for _ in ()).throw(AssertionError('state read'))
    report = obj.run(RunMode.SMOKE_TEST)
    assert report.exit_code == 0
    assert report.delivered == 1
    assert messages == [payload]
    assert not (tmp_path/'state.json').exists()


def test_smoke_requires_manual_event(tmp_path):
    obj = tracker(tmp_path, {})
    report = obj.run(RunMode.SMOKE_TEST, event='schedule')
    assert report.exit_code == 2


def test_invalid_recovery_confirmation_before_network(tmp_path):
    obj = tracker(tmp_path, {'JOB_TRACKER_LIVE_ENABLED':'false', 'JOB_TRACKER_RECOVERY_RUN_ID':'run-1'})
    report = obj.run(RunMode.RECOVER_DELIVERY)
    assert report.exit_code == 2
    assert not report.delta_ready
