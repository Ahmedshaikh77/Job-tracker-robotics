from copy import deepcopy
from datetime import datetime, timezone

from src.reporting import summarize_same_run, metrics_from_state, RunReport


def test_same_run_thresholds():
    assert summarize_same_run(eligible=100, within_target=95).target_met is True
    assert summarize_same_run(eligible=100, within_target=94).target_met is False
    assert summarize_same_run(eligible=0, within_target=0).rate is None


def test_metrics_count_only_exact_run_durable_timely_receipts():
    stamp = '2026-09-07T20:00:00Z'
    state = {'runs': {'run1': {'eligible_immediate_revision_ids': ['ok','late','negative','missing']}},
             'delivery': {'pending_immediate': {}, 'delivered': {}}, 'digest': {'pending_moderate': {}}}
    for rid, delivered in [('ok','2026-09-07T20:10:00Z'), ('late','2026-09-07T20:10:01Z'),
                            ('negative','2026-09-07T19:59:00Z'), ('other','2026-09-07T20:01:00Z')]:
        state['delivery']['delivered'][rid] = {'queued_run_id': 'run1', 'fetch_completed_at': stamp, 'delivered_at': delivered}
    metrics = metrics_from_state(state, 'run1', datetime(2026,9,7,21,tzinfo=timezone.utc))
    assert metrics['eligible_immediate_revisions'] == 4
    assert metrics['same_run_delivered_within_target'] == 1
    assert metrics['same_run_delivery_rate'] == .25


def test_report_does_not_serialize_preview_payloads():
    report = RunReport(mode='dry-run', preview_items=('PRIVATE FULL DESCRIPTION',))
    encoded = report.to_dict()
    assert encoded['report_version'] == 1
    assert set(encoded) == {'report_version','mode','phase','run_id','exit_code','phase_succeeded','delta_ready',
                           'delta_has_changes','counts','timing','same_run','backlog','source_failures','delivery_error'}
    assert 'PRIVATE' not in str(encoded)
