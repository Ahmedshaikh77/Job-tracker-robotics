"""Explicit job tracker modes. User-supplied message text stays in environment data."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

from .orchestrator import JobTracker, RunMode, RunPhase, valid_run_id
from .reporting import RunReport


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description='Fresh engineering jobs and reliable Telegram alerts')
    parser.add_argument('--mode', choices=[m.value for m in RunMode], default='dry-run')
    parser.add_argument('--phase', choices=[p.value for p in RunPhase])
    parser.add_argument('--config', default='config.yaml')
    parser.add_argument('--profile', default='profile.yaml')
    parser.add_argument('--state', default='state.json')
    parser.add_argument('--run-id')
    parser.add_argument('--delta-json')
    parser.add_argument('--report-json')
    args = parser.parse_args(argv)
    stateful = args.mode in ('live','seed','recover-delivery')
    if (args.mode == 'live') != bool(args.phase):
        parser.error('--phase is required only for live mode')
    if stateful != bool(args.delta_json):
        parser.error('--delta-json is required only for stateful modes')
    if args.mode in ('live','seed') and not valid_run_id(args.run_id):
        parser.error('stateful mode requires a valid --run-id')
    if args.run_id and not valid_run_id(args.run_id):
        parser.error('invalid --run-id')
    if args.delta_json and args.report_json and Path(args.delta_json).resolve() == Path(args.report_json).resolve():
        parser.error('delta and report paths must differ')
    for output in (args.delta_json, args.report_json):
        if output and Path(output).resolve() in {Path(args.state).resolve(), Path(args.config).resolve(), Path(args.profile).resolve()}:
            parser.error('output path must not overwrite input')
    return args


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    try:
        args = parse_args(argv)
    except SystemExit as exc:
        if exc.code == 0:
            return 0
        # Argument errors still produce a safe report when a separate output was supplied.
        probe = argparse.ArgumentParser(add_help=False)
        probe.add_argument('--report-json'); probe.add_argument('--state', default='state.json')
        probe.add_argument('--config', default='config.yaml'); probe.add_argument('--profile', default='profile.yaml')
        known, _ = probe.parse_known_args(argv)
        if known.report_json and Path(known.report_json).resolve() not in {
                Path(known.state).resolve(), Path(known.config).resolve(), Path(known.profile).resolve()}:
            from .state import atomic_write_json
            atomic_write_json(Path(known.report_json), RunReport(mode='invalid', exit_code=2,
                              phase_succeeded=False, delivery_error='invalid-arguments').to_dict())
        return 2
    try:
        from .config import load_config
        from .profile import load_profile
        tracker = JobTracker(settings=load_config(args.config), profile=load_profile(args.profile),
                             state_path=args.state, delta_path=args.delta_json)
        report = tracker.run(args.mode, phase=args.phase, run_id=args.run_id,
                             event=os.environ.get('GITHUB_EVENT_NAME', 'manual'))
    except Exception as exc:
        report = RunReport(mode=args.mode, phase=args.phase, run_id=args.run_id or '', exit_code=2,
                           phase_succeeded=False, delivery_error='configuration-or-state-invalid:' + type(exc).__name__)
    if args.report_json:
        from .state import atomic_write_json
        atomic_write_json(Path(args.report_json), report.to_dict())
    print(json.dumps(report.to_dict(), sort_keys=True))
    if args.mode == 'dry-run':
        from .alert_formatting import format_alert_entry
        for item in report.preview_items:
            print(format_alert_entry(item))
    return report.exit_code


if __name__ == '__main__':
    raise SystemExit(main())
