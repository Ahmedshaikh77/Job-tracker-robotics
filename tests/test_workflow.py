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


def test_roundup_requires_manual_scan_and_cannot_leak_into_schedule():
    options = dict(branch='main', default_branch='main', live='true', current_roundup=True)
    assert validate_guard(mode='live', event='workflow_dispatch', **options) is True
    assert validate_guard(mode='dry-run', event='workflow_dispatch', **options) is True
    with pytest.raises(ValueError):
        validate_guard(mode='live', event='schedule', **options)
    with pytest.raises(ValueError):
        validate_guard(mode='smoke-test', event='workflow_dispatch', **options)


def test_roundup_dispatch_input_is_forwarded_only_to_scan_phase(tmp_path, monkeypatch):
    import json
    from src import workflow_tools
    commands = []
    monkeypatch.setenv('MODE', 'live')
    monkeypatch.setenv('RUN_ID', 'gha:1:1')
    monkeypatch.setenv('RUNNER_TEMP', str(tmp_path))
    monkeypatch.setenv('CURRENT_ROUNDUP', 'true')
    monkeypatch.setenv('INPUT_STATE', str(tmp_path / 'absent-state.json'))

    def run(command, check):
        commands.append(command)
        report = Path(command[command.index('--report-json') + 1])
        report.write_text(json.dumps({'delta_ready': False}))
        from types import SimpleNamespace
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(workflow_tools.subprocess, 'run', run)
    monkeypatch.setenv('PHASE_SLOT', 'primary')
    assert workflow_tools.run_phase() == 0
    assert '--current-roundup' in commands[-1]
    monkeypatch.setenv('PHASE_SLOT', 'delivery')
    assert workflow_tools.run_phase() == 0
    assert '--current-roundup' not in commands[-1]


def test_exact_queue_lag_and_estimated_slot_are_separate():
    report = schedule_timing({'created_at':'2026-09-07T20:23:00Z','run_started_at':'2026-09-07T20:25:00Z','event':'schedule'}, None)
    assert report['actions_queue_lag_seconds'] == 120
    assert report['estimated_slot_offset_seconds'] == 60
    assert report['estimated_missed_slot_gap_count'] is None


def test_schedule_timing_detects_missing_quarter_hour_slots():
    report = schedule_timing(
        {'created_at': '2026-09-07T20:23:00Z', 'run_started_at': '2026-09-07T20:25:00Z', 'event': 'schedule'},
        {'created_at': '2026-09-07T19:38:00Z'})
    assert report['estimated_missed_slot_gap_count'] == 2
    before_first = schedule_timing(
        {'created_at': '2026-09-07T20:03:00Z', 'run_started_at': '2026-09-07T20:03:00Z', 'event': 'schedule'}, None)
    assert before_first['estimated_slot_offset_seconds'] == 660


def test_workflow_contract():
    workflow = yaml.safe_load(Path('.github/workflows/check_jobs.yml').read_text())
    trigger = workflow.get('on', workflow.get(True))
    assert trigger['schedule'] == [{'cron':'7,22,37,52 * * * *'}]
    assert trigger['workflow_dispatch']['inputs']['mode']['type'] == 'choice'
    roundup = trigger['workflow_dispatch']['inputs']['current_roundup']
    assert roundup['type'] == 'boolean' and roundup['default'] is False
    assert workflow['concurrency'] == {'group':'job-tracker-production','cancel-in-progress':False}
    steps = workflow['jobs']['check']['steps']
    ids = [step.get('id') for step in steps]
    by_id = {step['id']: step for step in steps if 'id' in step}
    assert by_id['guard']['env']['REQUESTED_CURRENT_ROUNDUP'] == '${{ inputs.current_roundup }}'
    assert by_id['primary']['env']['CURRENT_ROUNDUP'] == '${{ steps.guard.outputs.current_roundup }}'
    assert 'CURRENT_ROUNDUP' not in by_id['delivery']['env']
    assert ids.index('tests') < ids.index('primary') < ids.index('queue_sync') < ids.index('delivery') < ids.index('receipt_sync')
    for step in steps:
        if step.get('id') not in ('primary','delivery'):
            assert 'TELEGRAM_BOT_TOKEN' not in step.get('env', {})
