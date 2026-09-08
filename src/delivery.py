"""At-least-once delivery from persisted intents, checkpointed after every chunk."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from .alert_formatting import build_message_chunks
from .models import QueueInvalidationReason


@dataclass(frozen=True, slots=True)
class DeliveryReport:
    attempted_chunks: int
    delivered_chunks: int
    delivered_revision_ids: tuple[str, ...]
    pending_revision_ids: tuple[str, ...]
    failed: bool
    error: str | None


def chunk_id_for(heading, revision_ids):
    return hashlib.sha256(json.dumps([heading, list(revision_ids)], separators=(',', ':')).encode()).hexdigest()


def deliver_pending(*, heading, queue_kind, notifier, state, now, revision_ids=None, message_limit=3900):
    delivered = []
    attempted = completed = 0
    if queue_kind not in ('immediate', 'moderate'):
        raise ValueError('Unknown queue kind')
    pending = state.pending_immediate if queue_kind == 'immediate' else state.pending_moderate

    def report(error=None):
        return DeliveryReport(attempted, completed, tuple(delivered),
                              tuple(i.revision_id for i in pending()), error is not None, error)

    if not state.is_persisted():
        return report('queue-not-durably-persisted')
    items = pending()
    by_id = {item.revision_id: item for item in items}
    if revision_ids is not None:
        if len(set(revision_ids)) != len(revision_ids) or any(rid not in by_id for rid in revision_ids):
            return report('invalid-pending-subset')
        items = tuple(by_id[rid] for rid in revision_ids)
    if any(item.candidate_id not in state.state['candidates'] for item in items):
        return report('pending-candidate-missing')
    stale = tuple(item for item in items if not state.queue_item_is_current(item))
    if stale:
        try:
            for item in stale:
                state.invalidate_candidate_queue(item.candidate_id, QueueInvalidationReason.DELIVERY_STALE, now)
            state.save_atomic()
        except Exception:
            return report('stale-queue-checkpoint-failed')
        items = tuple(item for item in items if state.queue_item_is_current(item))
    chunks = build_message_chunks(items, heading=heading, limit=message_limit)
    if chunks.quarantines:
        return report('alert-formatting-quarantine')
    for chunk in chunks.chunks:
        attempted += 1
        try:
            receipt = notifier.send_message(chunk.text)
            state.mark_chunk_delivered(chunk.revision_ids, receipt.delivered_at,
                                       chunk_id_for(heading, chunk.revision_ids), receipt.message_id)
            state.save_atomic()
        except Exception:
            return report('telegram-or-receipt-checkpoint-failed')
        completed += 1
        delivered.extend(chunk.revision_ids)
    return report()


def deliver_health_pending(*, notifier, state, now, delivery_id=None):
    def report(error=None, attempted=0, completed=0):
        return DeliveryReport(attempted, completed, (), (), error is not None, error)

    if not state.is_persisted():
        return report('health-intent-not-durably-persisted')
    items = state.pending_health_summaries()
    if delivery_id is not None:
        items = tuple(item for item in items if item.delivery_id == delivery_id)
        if not items:
            return report('health-intent-missing')
    attempted = completed = 0
    for item in items:
        attempted += 1
        try:
            receipt = notifier.send_message(item.text)
            state.mark_health_summary_delivered(item.delivery_id, receipt.delivered_at, receipt.message_id)
            state.save_atomic()
        except Exception:
            return report('health-delivery-or-checkpoint-failed', attempted, completed)
        completed += 1
    return report(attempted=attempted, completed=completed)
