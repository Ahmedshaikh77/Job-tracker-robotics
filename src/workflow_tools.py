"""Small GitHub Actions helpers. No shell interpolation of dispatch inputs."""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


def validate_guard(*, mode, event, branch, default_branch, live):
    if mode not in ('live','seed','dry-run','validate-only','smoke-test','recover-delivery'):
        raise ValueError('Invalid mode')
    if mode in ('live','seed','smoke-test','recover-delivery') and branch != default_branch:
        raise ValueError('Production actions require the default branch')
    if mode in ('smoke-test','recover-delivery') and event != 'workflow_dispatch':
        raise ValueError('This mode requires manual dispatch')
    if mode == 'recover-delivery' and live != 'false':
        raise ValueError('Recovery requires disabled live delivery')
    return mode != 'live' or live == 'true'


def _date(value):
    return datetime.fromisoformat(value.replace('Z','+00:00'))


def schedule_timing(current, prior):
    created = _date(current['created_at']); started = _date(current['run_started_at'])
    slots = [created.replace(minute=minute, second=0, microsecond=0) for minute in (17,47)]
    slots += [slot - timedelta(hours=1) for slot in slots]
    latest = max(slot for slot in slots if slot <= created)
    return {'actions_queue_lag_seconds': (started-created).total_seconds(),
            'estimated_slot_offset_seconds': (created-latest).total_seconds() if current['event']=='schedule' else None,
            'missed_slot_gap_count': max(0, int((created-_date(prior['created_at'])).total_seconds()//1800)-1)
                                    if prior and current['event']=='schedule' else None}


def _outputs(**values):
    path = os.environ.get('GITHUB_OUTPUT')
    if path:
        with open(path, 'a', encoding='utf-8') as stream:
            for key, value in values.items():
                value = str(value).lower() if isinstance(value,bool) else str(value)
                if '\n' in value or '\r' in value:
                    raise ValueError('Invalid workflow output')
                stream.write(f'{key}={value}\n')


def _git(*args, input=None, env=None):
    process = subprocess.run(['git', *args], input=input, capture_output=True, env=env)
    if process.returncode:
        raise RuntimeError('Git operation failed')
    return process.stdout


def guard():
    event = os.environ['GITHUB_EVENT_NAME']
    mode = 'live' if event == 'schedule' else os.environ.get('REQUESTED_MODE','dry-run')
    active = validate_guard(mode=mode, event=event, branch=os.environ['GITHUB_REF_NAME'],
             default_branch=os.environ['DEFAULT_BRANCH'], live=os.environ.get('JOB_TRACKER_LIVE_ENABLED',''))
    run_id = f"gha:{os.environ['GITHUB_RUN_ID']}:{os.environ.get('GITHUB_RUN_ATTEMPT','1')}"
    if mode == 'recover-delivery':
        from .orchestrator import valid_run_id
        run_id = os.environ.get('JOB_TRACKER_RECOVERY_RUN_ID','')
        if not valid_run_id(run_id) or os.environ.get('JOB_TRACKER_RECOVERY_CONFIRMATION') != 'SEND PENDING':
            raise ValueError('Recovery needs an exact run ID and confirmation')
    _outputs(mode=mode, active=active, run_id=run_id)


def run_phase():
    mode = os.environ['MODE']; slot = os.environ.get('PHASE_SLOT','primary')
    if mode == 'recover-delivery' and not os.environ.get('INPUT_STATE'):
        raise ValueError('Recovery requires a freshly loaded default-branch state')
    folder = Path(os.environ['RUNNER_TEMP']) / 'job-tracker-v2'
    folder.mkdir(parents=True, exist_ok=True)
    state_path = folder / f'{slot}.state.json'
    input_state = Path(os.environ.get('INPUT_STATE','state.json'))
    if input_state.exists():
        shutil.copyfile(input_state, state_path)
    report_path = folder / f'{slot}.report.json'
    delta_path = folder / f'{slot}.delta.json'
    command = [sys.executable,'-m','src.main','--mode',mode,'--state',str(state_path),'--report-json',str(report_path)]
    if mode == 'live':
        command += ['--phase','deliver' if slot == 'delivery' else 'prepare']
    if mode in ('live','seed'):
        command += ['--run-id',os.environ['RUN_ID']]
    if mode in ('live','seed','recover-delivery'):
        command += ['--delta-json',str(delta_path)]
    result = subprocess.run(command, check=False)
    report = json.loads(report_path.read_text()) if report_path.exists() else {}
    ready = report.get('delta_ready') is True and delta_path.exists()
    _outputs(status=result.returncode, delta_ready=ready, delta_path=delta_path,
             report_path=report_path, state_path=state_path)
    return result.returncode


def refresh():
    from .state import atomic_write_json, StateManager
    branch = os.environ['DEFAULT_BRANCH']
    _git('check-ref-format', f'refs/heads/{branch}')
    _git('fetch','origin',f'refs/heads/{branch}')
    raw = json.loads(_git('show','FETCH_HEAD:state.json'))
    destination = Path(os.environ['RUNNER_TEMP'])/'job-tracker-v2'/'recovery-input.state.json'
    atomic_write_json(destination,raw)
    state = StateManager.load(destination)
    if not state.is_persisted():
        raise ValueError('Recovery state must already use the current schema')
    _outputs(state_path=destination)


def sync_delta():
    """Replay one immutable delta atop fresh remote state with three normal pushes."""
    from .state_merge import StateDelta, apply_delta
    from .state import atomic_write_json
    from .config import load_config, build_state_limits
    limits = build_state_limits(load_config(os.environ.get('TRACKER_CONFIG','config.yaml')))
    delta_payload = json.loads(Path(os.environ['DELTA_PATH']).read_text())
    report = json.loads(Path(os.environ['REPORT_PATH']).read_text())
    delta = StateDelta.from_dict(delta_payload)
    expected_mode = os.environ['EXPECTED_DELTA_MODE']
    expected_id = os.environ['RUN_ID']
    if (not report.get('delta_ready') or delta_payload['mode'] != expected_mode
            or delta_payload['run_id'] != expected_id or report['run_id'] != expected_id
            or report['delta_has_changes'] != delta_payload['has_changes']):
        raise ValueError('Report and delta do not match the expected phase')
    expected_report_mode = 'live' if expected_mode.startswith('live-') else expected_mode
    if report['mode'] != expected_report_mode:
        raise ValueError('Unexpected report mode')
    branch = os.environ['DEFAULT_BRANCH']
    _git('check-ref-format',f'refs/heads/{branch}')
    destination = Path(os.environ['RUNNER_TEMP']) / 'job-tracker-v2' / 'committed.state.json'
    for attempt in range(3):
        _git('fetch','origin',f'refs/heads/{branch}')
        remote_commit = _git('rev-parse','FETCH_HEAD').decode().strip()
        remote = json.loads(_git('show',f'{remote_commit}:state.json'))
        merged = apply_delta(remote, delta, limits=limits)
        if merged == remote:
            atomic_write_json(destination, merged)
            _outputs(ready=True, state_path=destination, status=0)
            return 0
        payload = json.dumps(merged,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()
        blob = _git('hash-object','-w','--stdin',input=payload).decode().strip()
        with tempfile.TemporaryDirectory(prefix='tracker-index-') as folder:
            environment = dict(os.environ, GIT_INDEX_FILE=str(Path(folder)/'index'),
                GIT_AUTHOR_NAME='github-actions[bot]', GIT_COMMITTER_NAME='github-actions[bot]',
                GIT_AUTHOR_EMAIL='41898282+github-actions[bot]@users.noreply.github.com',
                GIT_COMMITTER_EMAIL='41898282+github-actions[bot]@users.noreply.github.com')
            _git('read-tree',remote_commit,env=environment)
            _git('update-index','--add','--cacheinfo',f'100644,{blob},state.json',env=environment)
            tree = _git('write-tree',env=environment).decode().strip()
            commit = _git('commit-tree',tree,'-p',remote_commit,input=f'chore: tracker {expected_mode} [skip ci]\n'.encode(),env=environment).decode().strip()
            process = subprocess.run(['git','push','origin',f'{commit}:refs/heads/{branch}'],capture_output=True)
            if process.returncode == 0:
                atomic_write_json(destination, merged)
                _outputs(ready=True, state_path=destination, status=0)
                return 0
    _outputs(ready=False, status=3)
    return 3


def summarize():
    """Read the final default-branch state, never a working post-send snapshot."""
    from .state import StateManager, atomic_write_json
    from .reporting import RunReport, metrics_from_state
    branch = os.environ['DEFAULT_BRANCH']
    _git('fetch','origin',f'refs/heads/{branch}')
    raw = json.loads(_git('show','FETCH_HEAD:state.json'))
    folder = Path(os.environ['RUNNER_TEMP'])/'job-tracker-v2'
    folder.mkdir(exist_ok=True)
    state_path = folder/'final.state.json'
    atomic_write_json(state_path,raw)
    state = StateManager.load(state_path)
    report = RunReport(mode=os.environ.get('MODE','live'), run_id=os.environ.get('RUN_ID',''),
                       **metrics_from_state(state.state,os.environ.get('RUN_ID',''),datetime.now(timezone.utc)))
    atomic_write_json(folder/'final.report.json',report.to_dict())
    summary = os.environ.get('GITHUB_STEP_SUMMARY')
    if summary:
        with open(summary,'a',encoding='utf-8') as stream:
            stream.write('## Durable delivery results\n\n```json\n'+json.dumps(report.to_dict(),indent=2)+'\n```\n')


def timing():
    repository = os.environ['GITHUB_REPOSITORY']; run_id = os.environ['GITHUB_RUN_ID']
    branch = os.environ['DEFAULT_BRANCH']
    current = json.loads(subprocess.check_output(['gh','api',f'repos/{repository}/actions/runs/{run_id}']))
    prior_runs = json.loads(subprocess.check_output(['gh','api',
        f'repos/{repository}/actions/workflows/check_jobs.yml/runs?event=schedule&branch={branch}&per_page=10']))['workflow_runs']
    prior = sorted((r for r in prior_runs if str(r['id']) != run_id and r['created_at'] < current['created_at']),
                   key=lambda r:r['created_at'],reverse=True)
    result = schedule_timing(current, prior[0] if prior else None)
    result.update(event=current['event'], head_branch=current['head_branch'])
    with open(os.environ['GITHUB_STEP_SUMMARY'],'a',encoding='utf-8') as stream:
        stream.write('\n## Schedule timing\n\nSlot offset is an estimate, separate from exact Actions queue lag.\n\n```json\n'
                     +json.dumps(result,indent=2)+'\n```\n')


def finish():
    failures = {name: os.environ.get(name,'') for name in ('PRIMARY_OUTCOME','QUEUE_SYNC_OUTCOME','DELIVERY_OUTCOME','RECEIPT_SYNC_OUTCOME')
                if os.environ.get(name) == 'failure'}
    if failures:
        print(json.dumps({'failed_phases':list(failures)}))
        return 3 if any('SYNC' in key for key in failures) else 1
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description='Guarded tracker workflow operations')
    parser.add_argument('command', choices=['guard','phase','refresh','sync','summarize','timing','finish'])
    args = parser.parse_args(argv)
    actions = {'guard':guard,'phase':run_phase,'refresh':refresh,'sync':sync_delta,'summarize':summarize,'timing':timing,'finish':finish}
    try:
        return actions[args.command]() or 0
    except Exception as exc:
        print(json.dumps({'error':'workflow-operation-failed','operation':args.command,'kind':type(exc).__name__}))
        return 3


if __name__ == '__main__':
    raise SystemExit(main())
