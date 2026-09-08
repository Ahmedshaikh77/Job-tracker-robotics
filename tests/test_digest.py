from datetime import date, datetime, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from src.digest import digest_due, run_moderate_digest, prepare_health_summary


def test_exact_local_boundary_and_same_day():
    zone = ZoneInfo('America/New_York')
    assert digest_due(datetime(2026,9,7,19,29,tzinfo=zone), None) is None
    assert digest_due(datetime(2026,9,7,19,30,tzinfo=zone), None) == date(2026,9,7)
    assert digest_due(datetime(2026,9,7,19,30,tzinfo=zone), '2026-09-07') is None


def test_dst_uses_local_time():
    assert digest_due(datetime(2026,3,9,23,30,tzinfo=timezone.utc), None) == date(2026,3,9)
    assert digest_due(datetime(2026,11,9,0,29,tzinfo=timezone.utc), None) is None
    assert digest_due(datetime(2026,11,9,0,30,tzinfo=timezone.utc), None) == date(2026,11,8)


class EmptyState:
    def __init__(self):
        self.state = {'digest': {'last_processed_date': None, 'last_health_summary_date': None}, 'sources': {}}
        self.events = []

    def is_persisted(self): return True
    def pending_moderate(self): return ()
    def pending_health_summaries(self): return ()
    def prune(self, now): pass
    def save_atomic(self): self.events.append('save')
    def complete_digest(self, day, at): self.events.append(('digest', day))
    def complete_health_summary(self, day, at): self.events.append(('health', day))


def test_empty_day_completes_and_saves_without_send():
    state = EmptyState()
    result = run_moderate_digest(state, None, datetime(2026,9,8,0,tzinfo=timezone.utc))
    assert result.completed_date == date(2026,9,7)
    assert state.events == [('digest','2026-09-07'), 'save']


def test_empty_health_day_records_completion_without_send():
    state = EmptyState()
    settings = SimpleNamespace(companies=(), digest=SimpleNamespace(timezone='America/New_York', send_after='19:30', persistent_health_warning_runs=3))
    assert prepare_health_summary(state, settings, datetime(2026,9,8,0,tzinfo=timezone.utc)) is None
    assert state.events == [('health','2026-09-07')]
