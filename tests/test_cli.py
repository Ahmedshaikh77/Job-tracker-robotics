import json
import pytest

from src.main import parse_args, main


@pytest.mark.parametrize('args', [
    ['--mode','live'], ['--mode','seed','--run-id','r'],
    ['--mode','dry-run','--delta-json','delta.json'],
    ['--mode','smoke-test','--phase','deliver'],
    ['--mode','live','--phase','prepare','--run-id','bad;command','--delta-json','d'],
])
def test_invalid_mode_arguments_rejected(args):
    with pytest.raises(SystemExit):
        parse_args(args)


def test_valid_live_arguments():
    args = parse_args(['--mode','live','--phase','prepare','--run-id','gha:1:1','--delta-json','d'])
    assert args.run_id == 'gha:1:1'


def test_argument_failure_writes_sanitized_report(tmp_path):
    output = tmp_path/'report.json'
    assert main(['--mode','live','--report-json',str(output)]) == 2
    report = json.loads(output.read_text())
    assert report['exit_code'] == 2
    assert not report['delta_ready']
