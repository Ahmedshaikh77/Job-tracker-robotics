"""Exercise real non-force Git synchronization using only temporary local repos."""
from datetime import datetime, timezone
from pathlib import Path
import json
import subprocess

from src.state import empty_state, atomic_write_json
from src.state_merge import build_delta, DeltaMode
from src.workflow_tools import sync_delta


def git(cwd, *args):
    return subprocess.check_output(['git','-C',str(cwd),*args],stderr=subprocess.DEVNULL).decode().strip()


def test_real_git_delta_sync_migration_and_idempotence(tmp_path,monkeypatch):
    bare = tmp_path/'remote.git'; repo = tmp_path/'checkout'; output = tmp_path/'runner'
    bare.mkdir(); repo.mkdir(); output.mkdir()
    git(bare,'init','--bare','--initial-branch=main')
    git(repo,'init','--initial-branch=main')
    git(repo,'config','user.name','Test'); git(repo,'config','user.email','test@example.test')
    atomic_write_json(repo/'state.json', {'seen_jobs':{},'company_failures':{}})
    (repo/'keep.txt').write_text('user data\n')
    git(repo,'add','state.json','keep.txt'); git(repo,'commit','-m','base')
    git(repo,'remote','add','origin',str(bare)); git(repo,'push','origin','main')
    now = datetime(2026,9,7,20,tzinfo=timezone.utc)
    base=empty_state(); final=empty_state()
    final['meta'].update(seeded_at=now.isoformat(),record_updated_at=now.isoformat())
    delta=build_delta(base,final,'seed-test',DeltaMode.SEED,now)
    atomic_write_json(tmp_path/'delta.json',delta.to_dict())
    atomic_write_json(tmp_path/'report.json',{'delta_ready':True,'delta_has_changes':True,'run_id':'seed-test','mode':'seed'})
    for name,value in {'DELTA_PATH':tmp_path/'delta.json','REPORT_PATH':tmp_path/'report.json','RUNNER_TEMP':output,
                       'EXPECTED_DELTA_MODE':'seed','RUN_ID':'seed-test','DEFAULT_BRANCH':'main'}.items():
        monkeypatch.setenv(name,str(value))
    monkeypatch.chdir(repo)
    assert sync_delta() == 0
    head=git(bare,'rev-parse','main')
    assert json.loads(git(bare,'show','main:state.json')) == final
    assert git(bare,'show','main:keep.txt') == 'user data'
    assert sync_delta() == 0
    assert git(bare,'rev-parse','main') == head
    assert json.loads((output/'job-tracker-v2'/'committed.state.json').read_text()) == final
