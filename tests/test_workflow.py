from pathlib import Path
import yaml
import pytest

from src.workflow_tools import validate_guard, schedule_timing


def test_default_branch_required_for_mutations():
    with pytest.raises(ValueError):
        validate_guard(mode='seed', event='workflow_dispatch', branch='feature', default_branch='main', live='false')


def test_disabled_live_exits_without_credentials():
    assert validate_guard(mode='live', event='schedule', branch='main', default_branch='main', live='false') is False


def test_recovery_only_when_explicitly_disabled():
    with pytest.raises(ValueError):
        validate_guard(mode='recover-delivery', event='workflow_dispatch', branch='main', default_branch='main', live='')


def test_exact_queue_lag_and_estimated_slot_are_separate():
    report = schedule_timing({'created_at':'2026-09-07T20:23:00Z','run_started_at':'2026-09-07T20:25:00Z','event':'schedule'}, None)
    assert report['actions_queue_lag_seconds'] == 120
    assert report['estimated_slot_offset_seconds'] == 360
    assert report['missed_slot_gap_count'] is None


def test_workflow_contract():
    workflow = yaml.safe_load(Path('.github/workflows/check_jobs.yml').read_text())
    trigger = workflow.get('on', workflow.get(True))
    assert trigger['schedule'] == [{'cron':'17,47 * * * *'}]
    assert trigger['workflow_dispatch']['inputs']['mode']['type'] == 'choice'
    assert workflow['concurrency'] == {'group':'job-tracker-production','cancel-in-progress':False}
    steps = workflow['jobs']['check']['steps']
    ids = [step.get('id') for step in steps]
    assert ids.index('tests') < ids.index('primary') < ids.index('queue_sync') < ids.index('delivery') < ids.index('receipt_sync')
    for step in steps:
        if step.get('id') not in ('primary','delivery'):
            assert 'TELEGRAM_BOT_TOKEN' not in step.get('env', {})
