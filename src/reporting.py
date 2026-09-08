"""Sanitized run reports and delivery metrics computed from durable receipts only."""
from __future__ import annotations

import argparse
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path


@dataclass(frozen=True, slots=True)
class SameRunMetric:
    eligible: int
    within_target: int
    rate: float | None
    target_met: bool | None


def summarize_same_run(*, eligible, within_target):
    if eligible < 0 or within_target < 0 or within_target > eligible:
        raise ValueError('Invalid delivery metric counts')
    rate = within_target / eligible if eligible else None
    return SameRunMetric(eligible, within_target, rate, rate >= .95 if rate is not None else None)


def _date(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00'))


def metrics_from_state(state, run_id, now):
    ledger = state.get('runs', {}).get(run_id, {})
    ids = set(ledger.get('eligible_immediate_revision_ids', ()))
    timely = 0
    latencies = []
    delivery_times = []
    for rid in ids:
        receipt = state['delivery']['delivered'].get(rid)
        if not receipt or receipt.get('queued_run_id') != run_id:
            continue
        latency = (_date(receipt['delivered_at']) - _date(receipt['fetch_completed_at'])).total_seconds()
        latencies.append(latency)
        delivery_times.append(receipt['delivered_at'])
        timely += 0 <= latency <= 600
    metric = summarize_same_run(eligible=len(ids), within_target=timely)
    pending = list(state['delivery']['pending_immediate'].values()) + list(state['digest']['pending_moderate'].values())
    ages = [max(0, (now - _date(item['queued_at'])).total_seconds()) for item in pending]
    return dict(eligible_immediate_revisions=metric.eligible, same_run_delivered_within_target=metric.within_target,
                same_run_delivery_rate=metric.rate, same_run_target_met=metric.target_met,
                fetch_completed_at=ledger.get('fetch_completed_at'),
                last_delivery_at=max(delivery_times, key=_date) if delivery_times else None,
                same_run_delivery_latency_seconds=max(latencies) if latencies else None,
                pending_backlog_count=len(pending), oldest_pending_age_seconds=max(ages) if ages else None)


@dataclass(frozen=True, slots=True)
class RunReport:
    mode: str
    phase: str | None = None
    report_version: int = 1
    exit_code: int = 0
    phase_succeeded: bool = True
    run_id: str = ''
    fetched: int = 0
    assessed: int = 0
    queued_immediate: int = 0
    queued_moderate: int = 0
    delivered: int = 0
    eligible_immediate_revisions: int = 0
    same_run_delivered_within_target: int = 0
    same_run_delivery_rate: float | None = None
    same_run_target_met: bool | None = None
    fetch_completed_at: str | None = None
    last_delivery_at: str | None = None
    same_run_delivery_latency_seconds: float | None = None
    pending_backlog_count: int = 0
    oldest_pending_age_seconds: float | None = None
    source_failures: tuple[str, ...] = ()
    delivery_error: str | None = None
    delta_ready: bool = False
    delta_has_changes: bool = False
    preview_items: tuple = ()

    def to_dict(self):
        return {
            'report_version': self.report_version, 'mode': self.mode, 'phase': self.phase,
            'run_id': self.run_id, 'exit_code': self.exit_code, 'phase_succeeded': self.phase_succeeded,
            'delta_ready': self.delta_ready, 'delta_has_changes': self.delta_has_changes,
            'counts': {key: getattr(self, key) for key in ('fetched','assessed','queued_immediate','queued_moderate','delivered')},
            'timing': {key: getattr(self, key) for key in ('fetch_completed_at','last_delivery_at','same_run_delivery_latency_seconds')},
            'same_run': {'eligible': self.eligible_immediate_revisions, 'within_target': self.same_run_delivered_within_target,
                         'rate': self.same_run_delivery_rate, 'target_met': self.same_run_target_met},
            'backlog': {'count': self.pending_backlog_count, 'oldest_age_seconds': self.oldest_pending_age_seconds},
            'source_failures': list(self.source_failures), 'delivery_error': self.delivery_error,
        }


def main(argv=None):
    parser = argparse.ArgumentParser(description='Report durable job tracker delivery metrics')
    sub = parser.add_subparsers(dest='command', required=True)
    summarize = sub.add_parser('summarize')
    summarize.add_argument('--state', required=True)
    summarize.add_argument('--run-id', required=True)
    summarize.add_argument('--output', required=True)
    args = parser.parse_args(argv)
    from .state import StateManager, atomic_write_json
    state = StateManager.load(args.state)
    report = RunReport(mode='live', phase='deliver', run_id=args.run_id,
                       **metrics_from_state(state.state, args.run_id, datetime.now(timezone.utc)))
    atomic_write_json(Path(args.output), report.to_dict())
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
