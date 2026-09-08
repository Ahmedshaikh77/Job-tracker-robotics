"""Versioned, transactional state for discovery and delivery."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any

from .dedupe import (
    candidate_aliases_from_legacy,
    canonical_candidate_id,
    durable_identity_aliases_from_legacy,
    normalize_official_url,
    normalize_text,
)
from .models import (
    AlertFact,
    AlertItem,
    EvidenceStatus,
    FactSource,
    FetchContext,
    PendingHealthSummary,
    QueueInvalidationReason,
    Recommendation,
)


class StateCorruptionError(RuntimeError):
    """Raised when persisted state cannot be trusted."""


@dataclass(frozen=True, slots=True)
class StateLimits:
    closed_candidate_days: int = 90
    delivered_days: int = 365
    delivered_limit: int = 10_000
    pending_max_age_days: int = 30
    digest_entry_days: int = 30
    health_event_limit: int = 30
    run_ledger_days: int = 14


def empty_state() -> dict[str, Any]:
    return {
        "schema_version": 2,
        "sources": {},
        "candidates": {},
        "delivery": {"pending_immediate": {}, "delivered": {}},
        "digest": {
            "pending_moderate": {},
            "pending_health_summaries": {},
            "delivered_health_summaries": {},
            "last_processed_date": None,
            "last_processed_at": None,
            "last_health_summary_date": None,
            "last_health_summary_at": None,
        },
        "runs": {},
        "meta": {
            "seeded_at": None,
            "last_health_checkpoint": None,
            "record_updated_at": None,
        },
    }


def canonical_state_hash(state: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        state, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _is_optional_string(value: Any) -> bool:
    return value is None or isinstance(value, str)


def validate_v1(raw: Any) -> None:
    if not isinstance(raw, dict):
        raise StateCorruptionError("legacy state must be an object")
    if "seen_jobs" not in raw or set(raw) - {"seen_jobs", "company_failures"}:
        raise StateCorruptionError("invalid version-1 state keys")
    if not isinstance(raw["seen_jobs"], dict):
        raise StateCorruptionError("version-1 seen_jobs must be an object")
    failures = raw.get("company_failures", {})
    if not isinstance(failures, dict):
        raise StateCorruptionError("version-1 company_failures must be an object")
    allowed = {"first_seen", "title", "location", "url", "alerted"}
    for old_key, item in raw["seen_jobs"].items():
        if not isinstance(old_key, str) or ":" not in old_key:
            raise StateCorruptionError("invalid version-1 job key")
        if not isinstance(item, dict) or set(item) - allowed:
            raise StateCorruptionError("invalid version-1 job record")
        if not isinstance(item.get("alerted"), bool):
            raise StateCorruptionError("invalid version-1 alerted flag")
        for key in ("first_seen", "title", "location", "url"):
            if not _is_optional_string(item.get(key)):
                raise StateCorruptionError(f"invalid version-1 {key}")
    for company, failure in failures.items():
        if not isinstance(company, str) or not isinstance(failure, dict):
            raise StateCorruptionError("invalid version-1 company failure")
        if "count" in failure and not isinstance(failure["count"], int):
            raise StateCorruptionError("invalid version-1 failure count")


def migrate_v1(raw: dict[str, Any]) -> dict[str, Any]:
    migrated = empty_state()
    for old_key, item in raw.get("seen_jobs", {}).items():
        if not item.get("alerted"):
            continue
        company, _, posting_id = old_key.partition(":")
        candidate_id = canonical_candidate_id(company, posting_id, item.get("url", ""))
        legacy_revision = f"{candidate_id}:0:legacy"
        first_seen = item.get("first_seen")
        migrated["candidates"][candidate_id] = {
            "first_seen_at": first_seen,
            "last_seen_at": first_seen,
            "last_verified_open_at": None,
            "closed_at": None,
            "last_closed_at": None,
            "reopened_at": None,
            "aliases": candidate_aliases_from_legacy(
                company, posting_id, item.get("url", "")
            ),
            "source_refs": {},
            "discovered_during_seed": False,
            "migration_baseline_pending": True,
            "migration_snapshot": {
                "company": normalize_text(company),
                "title": normalize_text(item.get("title", "")),
                "location": normalize_text(item.get("location", "")),
                "url": normalize_official_url(item.get("url", "")),
            },
            "reopen_generation": 0,
            "last_content_hash": None,
            "last_evaluation": None,
            "seed_baseline": None,
            "last_alert_basis": None,
            "last_queued_revision_id": None,
            "last_queue_invalidation": None,
            "record_updated_at": first_seen,
        }
        migrated["delivery"]["delivered"][legacy_revision] = {
            "delivered_at": first_seen,
            "candidate_id": candidate_id,
            "reopen_generation": 0,
            "chunk_id": "legacy-v1",
            "message_id": None,
            "queued_run_id": "legacy-v1",
            "fetch_completed_at": first_seen,
            "identity_aliases": durable_identity_aliases_from_legacy(
                company, posting_id, item.get("url", "")
            ),
            "source_key": None,
            "record_updated_at": first_seen,
        }
    return migrated


def _require_mapping(container: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = container.get(key)
    if not isinstance(value, dict):
        raise StateCorruptionError(f"state field {key} must be an object")
    return value


def validate_v2(raw: Any) -> None:
    if not isinstance(raw, dict):
        raise StateCorruptionError("state must be an object")
    required = {
        "schema_version",
        "sources",
        "candidates",
        "delivery",
        "digest",
        "runs",
        "meta",
    }
    if set(raw) != required:
        raise StateCorruptionError("invalid version-2 top-level keys")
    if raw.get("schema_version") != 2:
        raise StateCorruptionError("unsupported state schema")
    for key in ("sources", "candidates", "runs", "meta"):
        _require_mapping(raw, key)
    delivery = _require_mapping(raw, "delivery")
    if set(delivery) != {"pending_immediate", "delivered"}:
        raise StateCorruptionError("invalid delivery state")
    for key in delivery:
        _require_mapping(delivery, key)
    digest = _require_mapping(raw, "digest")
    expected_digest = {
        "pending_moderate",
        "pending_health_summaries",
        "delivered_health_summaries",
        "last_processed_date",
        "last_processed_at",
        "last_health_summary_date",
        "last_health_summary_at",
    }
    if set(digest) != expected_digest:
        raise StateCorruptionError("invalid digest state")
    for key in (
        "pending_moderate",
        "pending_health_summaries",
        "delivered_health_summaries",
    ):
        _require_mapping(digest, key)
    for queue in (delivery["pending_immediate"], digest["pending_moderate"]):
        for revision_id, item in queue.items():
            if not isinstance(item, dict) or item.get("revision_id") != revision_id:
                raise StateCorruptionError("invalid pending alert record")
            if item.get("record_updated_at") != item.get("queued_at"):
                raise StateCorruptionError("pending alert clock is inconsistent")
    for delivery_id, item in digest["pending_health_summaries"].items():
        if not isinstance(item, dict) or item.get("delivery_id") != delivery_id:
            raise StateCorruptionError("invalid pending health summary")
        if item.get("record_updated_at") != item.get("queued_at"):
            raise StateCorruptionError("pending health clock is inconsistent")


def parse_utc(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError("timestamp must be ISO-8601") from exc
    require_aware_utc(parsed)
    return parsed.astimezone(timezone.utc)


def require_aware_utc(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("datetime must be aware UTC")


def utc_iso(value: datetime) -> str:
    require_aware_utc(value)
    return value.astimezone(timezone.utc).isoformat()


def _fact_to_dict(fact: AlertFact) -> dict[str, str]:
    return {
        "value": fact.value,
        "status": fact.status.value,
        "provenance": fact.provenance.value,
    }


def _fact_from_dict(payload: Mapping[str, Any]) -> AlertFact:
    return AlertFact(
        value=str(payload["value"]),
        status=EvidenceStatus(payload["status"]),
        provenance=FactSource(payload["provenance"]),
    )


def alert_item_to_dict(item: AlertItem) -> dict[str, Any]:
    return {
        "revision_id": item.revision_id,
        "candidate_id": item.candidate_id,
        "reopen_generation": item.reopen_generation,
        "identity_aliases": list(item.identity_aliases),
        "source_key": item.source_key,
        "company": item.company,
        "title": item.title,
        "application_url": item.application_url,
        "location": _fact_to_dict(item.location),
        "work_arrangement": _fact_to_dict(item.work_arrangement),
        "posted_date": _fact_to_dict(item.posted_date),
        "first_seen_at": item.first_seen_at,
        "salary": _fact_to_dict(item.salary),
        "experience": _fact_to_dict(item.experience),
        "authorization": _fact_to_dict(item.authorization),
        "full_time": _fact_to_dict(item.full_time),
        "score": item.score,
        "recommendation": item.recommendation.value,
        "match_reason": item.match_reason,
        "important_gap": item.important_gap,
        "resume_filename": item.resume_filename,
        "role_family": item.role_family,
        "resume_reason": item.resume_reason,
        "queued_at": item.queued_at,
        "queued_run_id": item.queued_run_id,
        "fetch_completed_at": item.fetch_completed_at,
        "record_updated_at": item.queued_at,
    }


def alert_item_from_dict(payload: Mapping[str, Any]) -> AlertItem:
    if payload.get("record_updated_at") != payload.get("queued_at"):
        raise StateCorruptionError("pending alert clock is inconsistent")
    return AlertItem(
        revision_id=payload["revision_id"],
        candidate_id=payload["candidate_id"],
        reopen_generation=int(payload["reopen_generation"]),
        identity_aliases=tuple(payload["identity_aliases"]),
        source_key=payload["source_key"],
        company=payload["company"],
        title=payload["title"],
        application_url=payload["application_url"],
        location=_fact_from_dict(payload["location"]),
        work_arrangement=_fact_from_dict(payload["work_arrangement"]),
        posted_date=_fact_from_dict(payload["posted_date"]),
        first_seen_at=payload["first_seen_at"],
        salary=_fact_from_dict(payload["salary"]),
        experience=_fact_from_dict(payload["experience"]),
        authorization=_fact_from_dict(payload["authorization"]),
        full_time=_fact_from_dict(payload["full_time"]),
        score=int(payload["score"]),
        recommendation=Recommendation(payload["recommendation"]),
        match_reason=payload["match_reason"],
        important_gap=payload["important_gap"],
        resume_filename=payload["resume_filename"],
        role_family=payload["role_family"],
        resume_reason=payload["resume_reason"],
        queued_at=payload["queued_at"],
        queued_run_id=payload["queued_run_id"],
        fetch_completed_at=payload["fetch_completed_at"],
    )


def health_summary_to_dict(item: PendingHealthSummary) -> dict[str, str]:
    return {
        "delivery_id": item.delivery_id,
        "local_date": item.local_date,
        "text": item.text,
        "queued_at": item.queued_at,
        "record_updated_at": item.queued_at,
    }


def health_summary_from_dict(payload: Mapping[str, Any]) -> PendingHealthSummary:
    if payload.get("record_updated_at") != payload.get("queued_at"):
        raise StateCorruptionError("pending health clock is inconsistent")
    return PendingHealthSummary(
        payload["delivery_id"],
        payload["local_date"],
        payload["text"],
        payload["queued_at"],
    )


_ALERT_IDENTITY_FIELDS = (
    "revision_id",
    "candidate_id",
    "reopen_generation",
    "identity_aliases",
    "source_key",
    "company",
    "title",
    "application_url",
)


def assert_same_alert_identity(
    first: Mapping[str, Any], second: Mapping[str, Any]
) -> None:
    if any(first.get(key) != second.get(key) for key in _ALERT_IDENTITY_FIELDS):
        raise StateCorruptionError("duplicate alert records disagree")


def remove_undelivered_revisions_for_candidate(
    state: dict[str, Any], candidate_id: str, except_revision_id: str
) -> bool:
    changed = False
    queues = (
        state["delivery"]["pending_immediate"],
        state["digest"]["pending_moderate"],
    )
    for queue in queues:
        for revision_id, item in tuple(queue.items()):
            if (
                revision_id != except_revision_id
                and item.get("candidate_id") == candidate_id
            ):
                del queue[revision_id]
                changed = True
    return changed


def set_completion_if_newer(
    record: dict[str, Any],
    *,
    date_key: str,
    timestamp_key: str,
    local_date: str,
    completed_at: str,
) -> bool:
    try:
        parsed_date = date.fromisoformat(local_date)
    except (TypeError, ValueError) as exc:
        raise ValueError("local date must be YYYY-MM-DD") from exc
    parsed_timestamp = parse_utc(completed_at)
    old_date_value = record.get(date_key)
    old_timestamp_value = record.get(timestamp_key)
    if old_date_value is not None:
        old_pair = (date.fromisoformat(old_date_value), parse_utc(old_timestamp_value))
        if (parsed_date, parsed_timestamp) <= old_pair:
            return False
    record[date_key] = local_date
    record[timestamp_key] = completed_at
    return True


def upsert_run_eligibility(
    runs: dict[str, Any],
    run_id: str,
    revision_ids: Sequence[str],
    fetch_completed_at: str,
    created_at: str,
) -> bool:
    parse_utc(fetch_completed_at)
    parse_utc(created_at)
    existing = runs.get(run_id)
    if existing is None:
        runs[run_id] = {
            "eligible_immediate_revision_ids": sorted(set(revision_ids)),
            "started_at": created_at,
            "fetch_completed_at": fetch_completed_at,
            "record_updated_at": created_at,
        }
        return True
    updated = {
        "eligible_immediate_revision_ids": sorted(
            set(existing["eligible_immediate_revision_ids"]).union(revision_ids)
        ),
        "started_at": min(existing["started_at"], created_at, key=parse_utc),
        "fetch_completed_at": max(
            existing["fetch_completed_at"], fetch_completed_at, key=parse_utc
        ),
        "record_updated_at": max(
            existing["record_updated_at"], created_at, key=parse_utc
        ),
    }
    if updated == existing:
        return False
    runs[run_id] = updated
    return True


def _older_than(value: str | None, now: datetime, days: int) -> bool:
    return value is not None and parse_utc(value) < now - timedelta(days=days)


def prune_state(state: dict[str, Any], now: datetime, limits: StateLimits) -> bool:
    require_aware_utc(now)
    changed = False
    candidates = state["candidates"]
    for candidate_id, record in tuple(candidates.items()):
        if record.get("closed_at") and _older_than(
            record["closed_at"], now, limits.closed_candidate_days
        ):
            del candidates[candidate_id]
            changed = True
    for queue in (
        state["delivery"]["pending_immediate"],
        state["digest"]["pending_moderate"],
    ):
        for revision_id, item in tuple(queue.items()):
            if _older_than(item.get("queued_at"), now, limits.pending_max_age_days):
                del queue[revision_id]
                changed = True
    health_pending = state["digest"]["pending_health_summaries"]
    for delivery_id, item in tuple(health_pending.items()):
        if _older_than(item.get("queued_at"), now, limits.digest_entry_days):
            del health_pending[delivery_id]
            changed = True
    delivered_health = state["digest"]["delivered_health_summaries"]
    for delivery_id, item in tuple(delivered_health.items()):
        if _older_than(item.get("delivered_at"), now, limits.digest_entry_days):
            del delivered_health[delivery_id]
            changed = True
    delivered = state["delivery"]["delivered"]
    for revision_id, receipt in tuple(delivered.items()):
        if _older_than(receipt.get("delivered_at"), now, limits.delivered_days):
            del delivered[revision_id]
            changed = True
    if len(delivered) > limits.delivered_limit:
        keep = {
            key
            for key, _ in sorted(
                delivered.items(),
                key=lambda pair: pair[1].get("delivered_at") or "",
                reverse=True,
            )[: limits.delivered_limit]
        }
        for key in tuple(delivered):
            if key not in keep:
                del delivered[key]
                changed = True
    for run_id, record in tuple(state["runs"].items()):
        if _older_than(record.get("record_updated_at"), now, limits.run_ledger_days):
            del state["runs"][run_id]
            changed = True
    for source in state["sources"].values():
        events = source.get("health_events", [])
        if len(events) > limits.health_event_limit:
            source["health_events"] = events[-limits.health_event_limit :]
            changed = True
    return changed


def atomic_write_json(path: Path, state: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    replaced = False
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(
                state,
                handle,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            )
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
        replaced = True
    finally:
        if not replaced:
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass


class StateManager:
    def __init__(self, path: str | Path, state: dict[str, Any], limits: StateLimits):
        self.path = Path(path)
        self.state = state
        self.limits = limits
        self.dirty = False
        self._persisted_fingerprint = canonical_state_hash(state)

    @classmethod
    def load(
        cls, path: str | Path, limits: StateLimits = StateLimits()
    ) -> "StateManager":
        state_path = Path(path)
        if not state_path.exists():
            manager = cls(state_path, empty_state(), limits)
            manager.dirty = True
            manager._persisted_fingerprint = "missing"
            return manager
        try:
            raw = json.loads(state_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise StateCorruptionError(f"invalid JSON in {state_path.name}") from exc
        migrated = False
        if not isinstance(raw, dict):
            raise StateCorruptionError("state must be an object")
        if "schema_version" not in raw:
            validate_v1(raw)
            raw = migrate_v1(raw)
            migrated = True
        if raw.get("schema_version") != 2:
            raise StateCorruptionError("unsupported state schema")
        validate_v2(raw)
        manager = cls(state_path, raw, limits)
        manager._persisted_fingerprint = canonical_state_hash(raw)
        manager.dirty = migrated
        return manager

    def fetch_context(self, source: str, now: datetime) -> FetchContext:
        require_aware_utc(now)
        record = self.state["sources"].get(source, {})
        due_ids = frozenset(
            posting_id
            for posting_id, due_at in record.get("detail_retry_ids", {}).items()
            if parse_utc(due_at) <= now
        )
        return FetchContext(
            previous_etag=record.get("etag"),
            previous_fingerprint=record.get("fingerprint"),
            previous_active_ids=frozenset(record.get("active_ids", [])),
            detail_retry_ids=due_ids,
            force_full=bool(due_ids),
        )

    def queue_immediate(self, item: AlertItem) -> bool:
        return self._queue(item, destination="pending_immediate")

    def queue_moderate(self, item: AlertItem) -> bool:
        return self._queue(item, destination="pending_moderate")

    def _queue(self, item: AlertItem, destination: str) -> bool:
        delivery = self.state["delivery"]
        immediate = delivery["pending_immediate"]
        moderate = self.state["digest"]["pending_moderate"]
        if item.revision_id in delivery["delivered"]:
            return False
        if item.revision_id in immediate:
            return False
        if item.revision_id in moderate:
            if destination == "pending_moderate":
                return False
            existing = moderate.pop(item.revision_id)
            assert_same_alert_identity(existing, alert_item_to_dict(item))
            remove_undelivered_revisions_for_candidate(
                self.state, item.candidate_id, item.revision_id
            )
            immediate[item.revision_id] = existing
            self.dirty = True
            return True
        remove_undelivered_revisions_for_candidate(
            self.state, item.candidate_id, item.revision_id
        )
        target = moderate if destination == "pending_moderate" else immediate
        target[item.revision_id] = alert_item_to_dict(item)
        self.dirty = True
        return True

    def pending_immediate(self) -> tuple[AlertItem, ...]:
        values = self.state["delivery"]["pending_immediate"].values()
        return tuple(
            alert_item_from_dict(item)
            for item in sorted(
                values, key=lambda value: (value["queued_at"], value["revision_id"])
            )
        )

    def pending_moderate(self) -> tuple[AlertItem, ...]:
        values = self.state["digest"]["pending_moderate"].values()
        return tuple(
            alert_item_from_dict(item)
            for item in sorted(
                values, key=lambda value: (value["queued_at"], value["revision_id"])
            )
        )

    def candidate_id_for_source_ref(
        self, source_key: str, posting_id: str
    ) -> str | None:
        ref_key = f"{source_key}|{posting_id}"
        matches = sorted(
            candidate_id
            for candidate_id, record in self.state["candidates"].items()
            if ref_key in record.get("source_refs", {})
        )
        if len(matches) > 1:
            raise StateCorruptionError("source reference belongs to multiple candidates")
        return matches[0] if matches else None

    def invalidate_candidate_queue(
        self,
        candidate_id: str,
        reason: QueueInvalidationReason,
        invalidated_at: datetime,
        *,
        source_key: str | None = None,
    ) -> tuple[str, ...]:
        require_aware_utc(invalidated_at)
        record = self.state["candidates"][candidate_id]
        queues = (
            self.state["delivery"]["pending_immediate"],
            self.state["digest"]["pending_moderate"],
        )
        revision_ids = tuple(
            sorted(
                {
                    revision_id
                    for queue in queues
                    for revision_id, item in queue.items()
                    if item["candidate_id"] == candidate_id
                    and (source_key is None or item["source_key"] == source_key)
                }
            )
        )
        if not revision_ids:
            return ()
        for queue in queues:
            for revision_id in revision_ids:
                queue.pop(revision_id, None)
        invalidated_iso = utc_iso(invalidated_at)
        if record.get("last_queued_revision_id") in revision_ids:
            record["last_queued_revision_id"] = None
        prior = set(
            (record.get("last_queue_invalidation") or {}).get("revision_ids", ())
        )
        record["last_queue_invalidation"] = {
            "revision_ids": sorted(prior.union(revision_ids)),
            "reason": reason.value,
            "invalidated_at": invalidated_iso,
        }
        record["record_updated_at"] = invalidated_iso
        self.dirty = True
        return revision_ids

    def queue_item_is_current(self, item: AlertItem) -> bool:
        record = self.state["candidates"].get(item.candidate_id)
        if record is None:
            return False
        evaluation = record.get("last_evaluation") or {}
        invalidated_ids = set(
            (record.get("last_queue_invalidation") or {}).get("revision_ids", ())
        )
        return (
            record.get("closed_at") is None
            and evaluation.get("eligible") is True
            and evaluation.get("recommendation") != Recommendation.SKIP.value
            and record.get("last_queued_revision_id") == item.revision_id
            and item.revision_id not in invalidated_ids
        )

    def queue_health_summary(self, item: PendingHealthSummary) -> bool:
        pending = self.state["digest"]["pending_health_summaries"]
        delivered = self.state["digest"]["delivered_health_summaries"]
        if item.delivery_id in pending or item.delivery_id in delivered:
            return False
        pending[item.delivery_id] = health_summary_to_dict(item)
        self.dirty = True
        return True

    def pending_health_summaries(self) -> tuple[PendingHealthSummary, ...]:
        values = self.state["digest"]["pending_health_summaries"].values()
        return tuple(
            health_summary_from_dict(item)
            for item in sorted(
                values, key=lambda value: (value["queued_at"], value["delivery_id"])
            )
        )

    def mark_health_summary_delivered(
        self, delivery_id: str, delivered_at: str, message_id: int
    ) -> None:
        parse_utc(delivered_at)
        item = self.state["digest"]["pending_health_summaries"].pop(delivery_id)
        self.state["digest"]["delivered_health_summaries"][delivery_id] = {
            "local_date": item["local_date"],
            "delivered_at": delivered_at,
            "message_id": message_id,
            "record_updated_at": delivered_at,
        }
        self.dirty = True

    def record_run_eligibility(
        self,
        run_id: str,
        revision_ids: Sequence[str],
        fetch_completed_at: str,
        created_at: str,
    ) -> None:
        changed = upsert_run_eligibility(
            self.state["runs"],
            run_id,
            revision_ids,
            fetch_completed_at,
            created_at,
        )
        self.dirty = self.dirty or changed

    def mark_chunk_delivered(
        self,
        delivery_ids: Sequence[str],
        delivered_at: str,
        chunk_id: str,
        message_id: int,
    ) -> None:
        parse_utc(delivered_at)
        for revision_id in delivery_ids:
            immediate = self.state["delivery"]["pending_immediate"].get(revision_id)
            moderate = self.state["digest"]["pending_moderate"].get(revision_id)
            if immediate is not None and moderate is not None:
                assert_same_alert_identity(immediate, moderate)
            item = immediate or moderate
            if item is None:
                continue
            self.state["delivery"]["pending_immediate"].pop(revision_id, None)
            self.state["digest"]["pending_moderate"].pop(revision_id, None)
            self.state["delivery"]["delivered"][revision_id] = {
                "delivered_at": delivered_at,
                "candidate_id": item["candidate_id"],
                "reopen_generation": item["reopen_generation"],
                "chunk_id": chunk_id,
                "message_id": message_id,
                "queued_run_id": item["queued_run_id"],
                "fetch_completed_at": item["fetch_completed_at"],
                "identity_aliases": item["identity_aliases"],
                "source_key": item["source_key"],
                "record_updated_at": delivered_at,
            }
            self.dirty = True

    def complete_digest(self, local_date: str, completed_at: str) -> None:
        changed = set_completion_if_newer(
            self.state["digest"],
            date_key="last_processed_date",
            timestamp_key="last_processed_at",
            local_date=local_date,
            completed_at=completed_at,
        )
        self.dirty = self.dirty or changed

    def complete_health_summary(self, local_date: str, completed_at: str) -> None:
        changed = set_completion_if_newer(
            self.state["digest"],
            date_key="last_health_summary_date",
            timestamp_key="last_health_summary_at",
            local_date=local_date,
            completed_at=completed_at,
        )
        self.dirty = self.dirty or changed

    def prune(self, now: datetime) -> None:
        self.dirty = prune_state(self.state, now, self.limits) or self.dirty

    def persisted_fingerprint(self) -> str:
        return self._persisted_fingerprint

    def is_persisted(self) -> bool:
        return (
            not self.dirty
            and canonical_state_hash(self.state) == self._persisted_fingerprint
        )

    def save_atomic(self) -> None:
        if not self.dirty:
            return
        validate_v2(self.state)
        atomic_write_json(self.path, self.state)
        self._persisted_fingerprint = canonical_state_hash(self.state)
        self.dirty = False
