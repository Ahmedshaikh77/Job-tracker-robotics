from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from src.delivery import deliver_pending, chunk_id_for
from src.alerts import TelegramReceipt, TelegramTransientError
from tests.test_alert_formatting import alert_item

NOW = datetime(2026, 9, 7, 20, tzinfo=timezone.utc)


class State:
    def __init__(self, items):
        self.items = {i.revision_id: i for i in items}
        self.state = {'candidates': {i.candidate_id: {} for i in items}}
        self.stale = set()
        self.dirty = False
        self.events = []
        self.saves = 0
        self.fail_save = False

    def is_persisted(self):
        return not self.dirty

    def pending_immediate(self):
        return tuple(self.items.values())

    def pending_moderate(self):
        return ()

    def queue_item_is_current(self, item):
        return item.revision_id not in self.stale

    def invalidate_candidate_queue(self, candidate_id, reason, now):
        self.items = {k: v for k, v in self.items.items() if v.candidate_id != candidate_id}
        self.dirty = True

    def mark_chunk_delivered(self, ids, delivered_at, chunk_id, message_id):
        for rid in ids:
            del self.items[rid]
        self.dirty = True

    def save_atomic(self):
        if self.fail_save:
            raise OSError('disk failed')
        self.saves += 1
        self.events.append('save')
        self.dirty = False


class Notifier:
    def __init__(self, state, fail_at=None):
        self.state = state
        self.messages = []
        self.fail_at = fail_at

    def send_message(self, text):
        self.state.events.append('send')
        if len(self.messages) == self.fail_at:
            raise TelegramTransientError('unavailable')
        self.messages.append(text)
        return TelegramReceipt(len(self.messages), NOW.isoformat())


def test_dirty_queue_never_sends(alert_item):
    state = State([alert_item]); state.dirty = True
    notifier = Notifier(state)
    report = deliver_pending(heading='Matches', queue_kind='immediate', notifier=notifier, state=state, now=NOW)
    assert report.failed
    assert notifier.messages == []


def test_each_receipt_is_saved_before_next_send(alert_item):
    items = [replace(alert_item, revision_id=str(i), candidate_id=str(i)) for i in range(5)]
    state = State(items); notifier = Notifier(state)
    report = deliver_pending(heading='Matches', queue_kind='immediate', notifier=notifier, state=state, now=NOW, message_limit=1600)
    assert not report.failed
    assert report.delivered_revision_ids == tuple(str(i) for i in range(5))
    assert state.events == ['send', 'save'] * 5


def test_partial_failure_keeps_later_jobs_pending(alert_item):
    items = [replace(alert_item, revision_id=str(i), candidate_id=str(i)) for i in range(3)]
    state = State(items); notifier = Notifier(state, fail_at=1)
    report = deliver_pending(heading='Matches', queue_kind='immediate', notifier=notifier, state=state, now=NOW, message_limit=1600)
    assert report.failed
    assert report.delivered_revision_ids == ('0',)
    assert tuple(state.items) == ('1', '2')


def test_unpersisted_receipt_not_counted(alert_item):
    state = State([alert_item]); state.fail_save = True
    notifier = Notifier(state)
    report = deliver_pending(heading='Matches', queue_kind='immediate', notifier=notifier, state=state, now=NOW)
    assert report.failed
    assert report.delivered_revision_ids == ()


def test_stale_removals_persist_before_other_sends(alert_item):
    good = replace(alert_item, revision_id='good', candidate_id='good')
    state = State([alert_item, good]); state.stale.add(alert_item.revision_id)
    notifier = Notifier(state)
    report = deliver_pending(heading='Matches', queue_kind='immediate', notifier=notifier, state=state, now=NOW)
    assert not report.failed
    assert state.events == ['save', 'send', 'save']
    assert report.delivered_revision_ids == ('good',)


def test_unknown_subset_fails_closed(alert_item):
    state = State([alert_item]); notifier = Notifier(state)
    report = deliver_pending(heading='Matches', queue_kind='immediate', notifier=notifier, state=state, now=NOW, revision_ids=('wrong',))
    assert report.failed
    assert notifier.messages == []


def test_chunk_identifier_stable_and_ordered():
    assert chunk_id_for('Matches', ('a','b')) == chunk_id_for('Matches', ('a','b'))
    assert chunk_id_for('Matches', ('a','b')) != chunk_id_for('Matches', ('b','a'))
