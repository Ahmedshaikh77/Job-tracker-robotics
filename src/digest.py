"""Daily New York-time digests, with durable completion markers."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo

from .alert_formatting import html_escape
from .delivery import DeliveryReport, deliver_pending, deliver_health_pending
from .models import PendingHealthSummary


def _iso(now):
    return now.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')


def digest_due(now, last_processed_date=None, *, timezone_name='America/New_York', send_after='19:30'):
    if now.tzinfo is None:
        raise ValueError('Digest time must be timezone-aware')
    local = now.astimezone(ZoneInfo(timezone_name))
    day = local.date()
    if local.time().replace(tzinfo=None) < time.fromisoformat(send_after):
        return None
    if last_processed_date and last_processed_date >= day.isoformat():
        return None
    return day


def health_summary_due(now, last_processed_date=None, **kwargs):
    return digest_due(now, last_processed_date, **kwargs)


@dataclass(frozen=True, slots=True)
class DigestReport:
    completed_date: date | None
    delivery: DeliveryReport | None = None
    failed: bool = False


def run_moderate_digest(state, notifier, now, *, policy=None, message_limit=3900):
    options = {} if policy is None else {'timezone_name': policy.timezone, 'send_after': policy.send_after}
    day = digest_due(now, state.state['digest'].get('last_processed_date'), **options)
    if day is None:
        return DigestReport(None)
    if not state.is_persisted():
        return DigestReport(None, failed=True)
    try:
        state.prune(now)
        if not state.is_persisted():
            state.save_atomic()
        due_ids = tuple(item.revision_id for item in state.pending_moderate()
                        if datetime.fromisoformat(item.queued_at.replace('Z','+00:00')) <= now)
        report = None
        if due_ids:
            report = deliver_pending(heading='Daily moderate matches', queue_kind='moderate', notifier=notifier,
                                     state=state, now=now, revision_ids=due_ids, message_limit=message_limit)
            if report.failed:
                return DigestReport(None, report, True)
        state.complete_digest(day.isoformat(), _iso(now))
        state.save_atomic()
        return DigestReport(day, report)
    except Exception:
        return DigestReport(None, failed=True)


def prepare_health_summary(state, settings, now):
    if state.pending_health_summaries():
        return None
    day = health_summary_due(now, state.state['digest'].get('last_health_summary_date'),
                            timezone_name=settings.digest.timezone, send_after=settings.digest.send_after)
    if day is None:
        return None
    warnings = []
    for source in settings.companies:
        if not source.enabled or source.priority != 'high':
            continue
        from .fetchers.base import source_key
        key = source_key(source.as_fetcher_mapping())
        record = state.state['sources'].get(key, {})
        circuit = record.get('circuit', record.get('circuit_state', 'closed'))
        if isinstance(circuit, dict):
            circuit = circuit.get('state', 'closed')
        failures = record.get('consecutive_failures', record.get('failure_streak', 0))
        if circuit in ('open', 'half-open') or failures >= settings.digest.persistent_health_warning_runs:
            warnings.append(f'{html_escape(source.name)}: feed needs attention')
    if not warnings:
        state.complete_health_summary(day.isoformat(), _iso(now))
        return None
    text = '<b>Job tracker source health</b>\n' + '\n'.join(warnings)
    text += '\nOther healthy sources continue to be checked.'
    delivery_id = f'health:{day.isoformat()}:{hashlib.sha256(text.encode()).hexdigest()[:20]}'
    item = PendingHealthSummary(delivery_id, day.isoformat(), text, _iso(now))
    state.queue_health_summary(item)
    return item


def deliver_health_summary(state, notifier, now):
    pending = state.pending_health_summaries()
    if not pending:
        return DigestReport(None)
    item = pending[0]
    report = deliver_health_pending(notifier=notifier, state=state, now=now, delivery_id=item.delivery_id)
    if report.failed:
        return DigestReport(None, report, True)
    try:
        state.complete_health_summary(item.local_date, _iso(now))
        state.save_atomic()
    except Exception:
        return DigestReport(None, report, True)
    return DigestReport(date.fromisoformat(item.local_date), report)
