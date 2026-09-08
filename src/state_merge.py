"""Immutable, deterministic state deltas for two-phase workflow merges."""

from __future__ import annotations

import argparse
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, fields
from datetime import date, datetime, timedelta, timezone
from enum import StrEnum
import json
from pathlib import Path
import sys
from types import MappingProxyType
from typing import Any

from .state import (
    StateLimits,
    atomic_write_json,
    canonical_state_hash,
    migrate_v1,
    parse_utc,
    prune_state,
    set_completion_if_newer,
    validate_v1,
    validate_v2,
)


class DeltaMode(StrEnum):
    LIVE_PREPARE = "live-prepare"
    LIVE_DELIVER = "live-deliver"
    SEED = "seed"
    RECOVER_DELIVERY = "recover-delivery"


_MAPPING_FIELDS = (
    "source_upserts",
    "candidate_upserts",
    "candidate_removals",
    "run_upserts",
    "run_removals",
    "pending_immediate_upserts",
    "pending_moderate_upserts",
    "pending_removals",
    "pending_health_upserts",
    "pending_health_removals",
    "delivered_health_upserts",
    "delivered_health_removals",
    "delivered_upserts",
    "delivered_removals",
    "meta_upserts",
)


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, tuple):
        return tuple(_freeze(item) for item in value)
    return value


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


def _utc_z(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("created_at must be timezone aware")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _validate_completion(value: Any, label: str) -> None:
    if value is None:
        return
    if not isinstance(value, Mapping) or set(value) != {"local_date", "completed_at"}:
        raise ValueError(f"{label} has invalid keys")
    try:
        date.fromisoformat(value["local_date"])
        parse_utc(value["completed_at"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"{label} is invalid") from exc


def _validate_removal_map(
    values: Mapping[str, Any],
    *,
    keys: set[str],
    reasons: set[str],
    identity_key: str | None = None,
) -> None:
    for map_key, value in values.items():
        if not isinstance(value, Mapping) or set(value) != keys:
            raise ValueError("delta removal has invalid keys")
        if value.get("reason") not in reasons:
            raise ValueError("delta removal has invalid reason")
        parse_utc(value["removed_at"])
        if identity_key is not None and value.get(identity_key) != map_key:
            raise ValueError("delta removal identity mismatch")


@dataclass(frozen=True, slots=True)
class StateDelta:
    delta_version: int
    state_schema_version: int
    run_id: str
    mode: DeltaMode
    created_at: str
    has_changes: bool
    source_upserts: Mapping[str, Any]
    candidate_upserts: Mapping[str, Any]
    candidate_removals: Mapping[str, Any]
    run_upserts: Mapping[str, Any]
    run_removals: Mapping[str, Any]
    pending_immediate_upserts: Mapping[str, Any]
    pending_moderate_upserts: Mapping[str, Any]
    pending_removals: Mapping[str, Any]
    pending_health_upserts: Mapping[str, Any]
    pending_health_removals: Mapping[str, Any]
    delivered_health_upserts: Mapping[str, Any]
    delivered_health_removals: Mapping[str, Any]
    digest_completion: Mapping[str, Any] | None
    health_summary_completion: Mapping[str, Any] | None
    delivered_upserts: Mapping[str, Any]
    delivered_removals: Mapping[str, Any]
    meta_upserts: Mapping[str, Any]

    def __post_init__(self) -> None:
        if self.delta_version != 1 or self.state_schema_version != 2:
            raise ValueError("unsupported state delta version")
        if not isinstance(self.run_id, str) or not self.run_id.strip():
            raise ValueError("delta run_id must be nonempty")
        object.__setattr__(self, "mode", DeltaMode(self.mode))
        try:
            parsed_input = datetime.fromisoformat(
                self.created_at.replace("Z", "+00:00")
            )
        except (AttributeError, ValueError) as exc:
            raise ValueError("delta created_at must be UTC") from exc
        if parsed_input.tzinfo is None or parsed_input.utcoffset() != timedelta(0):
            raise ValueError("delta created_at must be UTC")
        parse_utc(self.created_at)
        for name in _MAPPING_FIELDS:
            value = getattr(self, name)
            if not isinstance(value, Mapping):
                raise ValueError(f"delta field {name} must be an object")
            object.__setattr__(self, name, _freeze(value))
        if self.digest_completion is not None:
            object.__setattr__(self, "digest_completion", _freeze(self.digest_completion))
        if self.health_summary_completion is not None:
            object.__setattr__(
                self,
                "health_summary_completion",
                _freeze(self.health_summary_completion),
            )
        _validate_completion(self.digest_completion, "digest_completion")
        _validate_completion(
            self.health_summary_completion, "health_summary_completion"
        )
        self._validate_removals()
        computed = any(bool(getattr(self, name)) for name in _MAPPING_FIELDS) or (
            self.digest_completion is not None
            or self.health_summary_completion is not None
        )
        if self.has_changes is not computed:
            raise ValueError("delta has_changes does not match its mutations")

    def _validate_removals(self) -> None:
        _validate_removal_map(
            self.candidate_removals,
            keys={"removed_at", "reason"},
            reasons={"expired"},
        )
        _validate_removal_map(
            self.run_removals,
            keys={"removed_at", "reason"},
            reasons={"expired"},
        )
        _validate_removal_map(
            self.delivered_removals,
            keys={"removed_at", "reason"},
            reasons={"expired", "over-cap"},
        )
        _validate_removal_map(
            self.delivered_health_removals,
            keys={"removed_at", "reason"},
            reasons={"expired"},
        )
        _validate_removal_map(
            self.pending_health_removals,
            keys={"delivery_id", "removed_at", "reason"},
            reasons={"delivered", "expired"},
            identity_key="delivery_id",
        )
        for map_key, value in self.pending_removals.items():
            expected = {
                "queue_kind",
                "revision_id",
                "removed_at",
                "reason",
                "replacement_revision_id",
            }
            if not isinstance(value, Mapping) or set(value) != expected:
                raise ValueError("pending removal has invalid keys")
            if value["queue_kind"] not in {"immediate", "moderate"}:
                raise ValueError("pending removal has invalid queue kind")
            if map_key != f"{value['queue_kind']}:{value['revision_id']}":
                raise ValueError("pending removal identity mismatch")
            if value["reason"] not in {
                "delivered",
                "promoted",
                "superseded",
                "invalidated",
                "expired",
            }:
                raise ValueError("pending removal has invalid reason")
            replacement = value["replacement_revision_id"]
            if value["reason"] in {"promoted", "superseded"}:
                if not isinstance(replacement, str) or not replacement:
                    raise ValueError("replacement revision is required")
            elif replacement is not None:
                raise ValueError("replacement revision must be null")
            parse_utc(value["removed_at"])

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "StateDelta":
        if not isinstance(payload, Mapping):
            raise ValueError("state delta must be an object")
        expected = {item.name for item in fields(cls)}
        if set(payload) != expected:
            missing = sorted(expected - set(payload))
            unknown = sorted(set(payload) - expected)
            raise ValueError(
                f"invalid state delta keys: missing={missing}, unknown={unknown}"
            )
        return cls(**dict(payload))

    def to_dict(self) -> dict[str, Any]:
        return {
            "delta_version": self.delta_version,
            "state_schema_version": self.state_schema_version,
            "run_id": self.run_id,
            "mode": self.mode.value,
            "created_at": self.created_at,
            "has_changes": self.has_changes,
            **{name: _thaw(getattr(self, name)) for name in _MAPPING_FIELDS[:12]},
            "digest_completion": _thaw(self.digest_completion),
            "health_summary_completion": _thaw(self.health_summary_completion),
            **{name: _thaw(getattr(self, name)) for name in _MAPPING_FIELDS[12:]},
        }


def _changed_upserts(
    base: Mapping[str, Any], final: Mapping[str, Any]
) -> dict[str, Any]:
    return {
        key: deepcopy(value)
        for key, value in final.items()
        if key not in base or base[key] != value
    }


def _completion_delta(
    base: Mapping[str, Any],
    final: Mapping[str, Any],
    date_key: str,
    timestamp_key: str,
) -> dict[str, str] | None:
    old_pair = (base.get(date_key), base.get(timestamp_key))
    new_pair = (final.get(date_key), final.get(timestamp_key))
    if old_pair == new_pair:
        return None
    if new_pair[0] is None or new_pair[1] is None:
        raise ValueError("completion fields cannot be cleared or split")
    probe = {date_key: old_pair[0], timestamp_key: old_pair[1]}
    if not set_completion_if_newer(
        probe,
        date_key=date_key,
        timestamp_key=timestamp_key,
        local_date=new_pair[0],
        completed_at=new_pair[1],
    ):
        raise ValueError("completion fields cannot move backward")
    return {"local_date": new_pair[0], "completed_at": new_pair[1]}


def _queue_maps(state: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    return {
        "immediate": state["delivery"]["pending_immediate"],
        "moderate": state["digest"]["pending_moderate"],
    }


def _replacement_revision(
    final: Mapping[str, Any], candidate_id: str, old_revision: str
) -> str | None:
    matches = sorted(
        revision_id
        for queue in _queue_maps(final).values()
        for revision_id, item in queue.items()
        if revision_id != old_revision and item.get("candidate_id") == candidate_id
    )
    if len(matches) > 1:
        raise ValueError("candidate has multiple replacement revisions")
    return matches[0] if matches else None


def _pending_removals(
    base: Mapping[str, Any],
    final: Mapping[str, Any],
    pruned_base: Mapping[str, Any],
    removed_at: str,
) -> dict[str, Any]:
    removals: dict[str, Any] = {}
    base_queues = _queue_maps(base)
    final_queues = _queue_maps(final)
    pruned_queues = _queue_maps(pruned_base)
    for kind, queue in base_queues.items():
        for revision_id, item in queue.items():
            if revision_id in final_queues[kind]:
                continue
            replacement: str | None = None
            if kind == "moderate" and revision_id in final_queues["immediate"]:
                reason = "promoted"
                replacement = revision_id
            elif revision_id in final["delivery"]["delivered"]:
                reason = "delivered"
            else:
                replacement = _replacement_revision(
                    final, item.get("candidate_id"), revision_id
                )
                if replacement is not None:
                    reason = "superseded"
                else:
                    candidate = final["candidates"].get(item.get("candidate_id"), {})
                    invalidated = set(
                        (candidate.get("last_queue_invalidation") or {}).get(
                            "revision_ids", ()
                        )
                    )
                    if revision_id in invalidated:
                        reason = "invalidated"
                    elif revision_id not in pruned_queues[kind]:
                        reason = "expired"
                    else:
                        raise ValueError(
                            f"unexplained pending disappearance: {kind}:{revision_id}"
                        )
            removals[f"{kind}:{revision_id}"] = {
                "queue_kind": kind,
                "revision_id": revision_id,
                "removed_at": removed_at,
                "reason": reason,
                "replacement_revision_id": replacement,
            }
    return removals


def _retention_removals(
    base: Mapping[str, Any],
    final: Mapping[str, Any],
    pruned_base: Mapping[str, Any],
    section: str,
    removed_at: str,
) -> dict[str, Any]:
    removals = {}
    for key in base[section]:
        if key not in final[section]:
            if key in pruned_base[section]:
                raise ValueError(f"unexplained {section} disappearance: {key}")
            removals[key] = {"removed_at": removed_at, "reason": "expired"}
    return removals


def _delivered_removals(
    base: Mapping[str, Any],
    final: Mapping[str, Any],
    pruned_base: Mapping[str, Any],
    created_at: datetime,
    removed_at: str,
    limits: StateLimits,
) -> dict[str, Any]:
    removals = {}
    for key, receipt in base["delivery"]["delivered"].items():
        if key in final["delivery"]["delivered"]:
            continue
        if key in pruned_base["delivery"]["delivered"]:
            raise ValueError(f"unexplained delivered disappearance: {key}")
        delivered_at = parse_utc(receipt["delivered_at"])
        reason = (
            "expired"
            if delivered_at < created_at - limits.delivered_days * _ONE_DAY
            else "over-cap"
        )
        removals[key] = {"removed_at": removed_at, "reason": reason}
    return removals


_ONE_DAY = timedelta(days=1)


def build_delta(
    base: Mapping[str, Any],
    final: Mapping[str, Any],
    run_id: str,
    mode: DeltaMode,
    created_at: datetime,
    *,
    limits: StateLimits = StateLimits(),
) -> StateDelta:
    """Build an exact, replayable semantic delta between valid snapshots."""
    validate_v2(base)
    validate_v2(final)
    if not run_id:
        raise ValueError("run_id must be nonempty")
    mode = DeltaMode(mode)
    created_z = _utc_z(created_at)
    pruned_base = deepcopy(base)
    prune_state(pruned_base, created_at.astimezone(timezone.utc), limits)

    removed_sources = set(base["sources"]) - set(final["sources"])
    if removed_sources:
        raise ValueError("source removal is not supported by state deltas")

    candidate_removals = _retention_removals(
        base, final, pruned_base, "candidates", created_z
    )
    run_removals = _retention_removals(
        base, final, pruned_base, "runs", created_z
    )
    pending_removals = _pending_removals(base, final, pruned_base, created_z)

    base_health = base["digest"]["pending_health_summaries"]
    final_health = final["digest"]["pending_health_summaries"]
    pruned_health = pruned_base["digest"]["pending_health_summaries"]
    pending_health_removals = {}
    for delivery_id in base_health:
        if delivery_id in final_health:
            continue
        if delivery_id in final["digest"]["delivered_health_summaries"]:
            reason = "delivered"
        elif delivery_id not in pruned_health:
            reason = "expired"
        else:
            raise ValueError(
                f"unexplained pending health disappearance: {delivery_id}"
            )
        pending_health_removals[delivery_id] = {
            "delivery_id": delivery_id,
            "removed_at": created_z,
            "reason": reason,
        }

    base_delivered_health = base["digest"]["delivered_health_summaries"]
    final_delivered_health = final["digest"]["delivered_health_summaries"]
    pruned_delivered_health = pruned_base["digest"]["delivered_health_summaries"]
    delivered_health_removals = {}
    for delivery_id in base_delivered_health:
        if delivery_id not in final_delivered_health:
            if delivery_id in pruned_delivered_health:
                raise ValueError(
                    f"unexplained delivered health disappearance: {delivery_id}"
                )
            delivered_health_removals[delivery_id] = {
                "removed_at": created_z,
                "reason": "expired",
            }

    delivered_removals = _delivered_removals(
        base, final, pruned_base, created_at, created_z, limits
    )
    digest_completion = _completion_delta(
        base["digest"],
        final["digest"],
        "last_processed_date",
        "last_processed_at",
    )
    health_summary_completion = _completion_delta(
        base["digest"],
        final["digest"],
        "last_health_summary_date",
        "last_health_summary_at",
    )
    meta_upserts = deepcopy(final["meta"]) if base["meta"] != final["meta"] else {}

    mutations: dict[str, Any] = {
        "source_upserts": _changed_upserts(base["sources"], final["sources"]),
        "candidate_upserts": _changed_upserts(
            base["candidates"], final["candidates"]
        ),
        "candidate_removals": candidate_removals,
        "run_upserts": _changed_upserts(base["runs"], final["runs"]),
        "run_removals": run_removals,
        "pending_immediate_upserts": _changed_upserts(
            base["delivery"]["pending_immediate"],
            final["delivery"]["pending_immediate"],
        ),
        "pending_moderate_upserts": _changed_upserts(
            base["digest"]["pending_moderate"],
            final["digest"]["pending_moderate"],
        ),
        "pending_removals": pending_removals,
        "pending_health_upserts": _changed_upserts(base_health, final_health),
        "pending_health_removals": pending_health_removals,
        "delivered_health_upserts": _changed_upserts(
            base_delivered_health, final_delivered_health
        ),
        "delivered_health_removals": delivered_health_removals,
        "delivered_upserts": _changed_upserts(
            base["delivery"]["delivered"], final["delivery"]["delivered"]
        ),
        "delivered_removals": delivered_removals,
        "meta_upserts": meta_upserts,
    }
    has_changes = any(bool(value) for value in mutations.values()) or (
        digest_completion is not None or health_summary_completion is not None
    )
    return StateDelta(
        1,
        2,
        run_id,
        mode,
        created_z,
        has_changes,
        digest_completion=digest_completion,
        health_summary_completion=health_summary_completion,
        **mutations,
    )


def _record_clock(record: Mapping[str, Any]) -> datetime:
    value = record.get("record_updated_at")
    return parse_utc(value) if value else datetime.min.replace(tzinfo=timezone.utc)


def _canonical_hash(record: Mapping[str, Any]) -> str:
    return canonical_state_hash(record)


def _choose_record(
    current: Mapping[str, Any], incoming: Mapping[str, Any]
) -> dict[str, Any]:
    current_clock = _record_clock(current)
    incoming_clock = _record_clock(incoming)
    if incoming_clock > current_clock:
        return deepcopy(dict(incoming))
    if current_clock > incoming_clock:
        return deepcopy(dict(current))
    winner = max((dict(current), dict(incoming)), key=_canonical_hash)
    return deepcopy(winner)


_SOURCE_INVENTORY_FIELDS = {
    "active_ids",
    "etag",
    "fingerprint",
    "source_total",
    "total_is_authoritative",
    "last_complete_at",
}


def _inventory_clock(record: Mapping[str, Any]) -> datetime:
    value = record.get("last_complete_at")
    return parse_utc(value) if value else datetime.min.replace(tzinfo=timezone.utc)


def _inventory_payload(record: Mapping[str, Any]) -> dict[str, Any]:
    return {key: deepcopy(record.get(key)) for key in _SOURCE_INVENTORY_FIELDS}


def _merge_source(
    current: Mapping[str, Any], incoming: Mapping[str, Any]
) -> dict[str, Any]:
    current_non = {
        key: value for key, value in current.items() if key not in _SOURCE_INVENTORY_FIELDS
    }
    incoming_non = {
        key: value for key, value in incoming.items() if key not in _SOURCE_INVENTORY_FIELDS
    }
    merged = _choose_record(current_non, incoming_non)
    current_clock = _inventory_clock(current)
    incoming_clock = _inventory_clock(incoming)
    if incoming_clock > current_clock:
        inventory = _inventory_payload(incoming)
    elif current_clock > incoming_clock:
        inventory = _inventory_payload(current)
    else:
        inventory = max(
            (_inventory_payload(current), _inventory_payload(incoming)),
            key=_canonical_hash,
        )
    merged.update(inventory)
    return merged


def _apply_upserts(
    target: dict[str, Any], upserts: Mapping[str, Any], *, source: bool = False
) -> None:
    for key, frozen in upserts.items():
        incoming = _thaw(frozen)
        current = target.get(key)
        if current is None:
            target[key] = incoming
        elif source:
            target[key] = _merge_source(current, incoming)
        else:
            target[key] = _choose_record(current, incoming)


def _tombstone_wins(record: Mapping[str, Any], removed_at: str) -> bool:
    return _record_clock(record) <= parse_utc(removed_at)


def _merge_run(current: Mapping[str, Any], incoming: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "eligible_immediate_revision_ids": sorted(
            set(current.get("eligible_immediate_revision_ids", ())).union(
                incoming.get("eligible_immediate_revision_ids", ())
            )
        ),
        "started_at": min(
            current["started_at"], incoming["started_at"], key=parse_utc
        ),
        "fetch_completed_at": max(
            current["fetch_completed_at"],
            incoming["fetch_completed_at"],
            key=parse_utc,
        ),
        "record_updated_at": max(
            current["record_updated_at"],
            incoming["record_updated_at"],
            key=parse_utc,
        ),
    }


_RECEIPT_IDENTITY = {
    "candidate_id",
    "reopen_generation",
    "source_key",
    "queued_run_id",
    "fetch_completed_at",
    "identity_aliases",
}


def _merge_receipt(
    current: Mapping[str, Any], incoming: Mapping[str, Any], *, health: bool = False
) -> dict[str, Any]:
    identity = {"local_date"} if health else _RECEIPT_IDENTITY
    if any(current.get(key) != incoming.get(key) for key in identity):
        raise ValueError("delivery receipts disagree on immutable identity")
    current_at = parse_utc(current["delivered_at"])
    incoming_at = parse_utc(incoming["delivered_at"])
    if incoming_at < current_at:
        return deepcopy(dict(incoming))
    if current_at < incoming_at:
        return deepcopy(dict(current))
    return deepcopy(max((dict(current), dict(incoming)), key=_canonical_hash))


def _apply_receipts(
    target: dict[str, Any], upserts: Mapping[str, Any], *, health: bool = False
) -> None:
    for key, frozen in upserts.items():
        incoming = _thaw(frozen)
        if not health and incoming.get("source_key") is None and incoming.get("chunk_id") != "legacy-v1":
            raise ValueError("live delivered receipt requires source_key")
        current = target.get(key)
        target[key] = (
            incoming
            if current is None
            else _merge_receipt(current, incoming, health=health)
        )


def _remote_to_v2(remote: Mapping[str, Any]) -> dict[str, Any]:
    raw = deepcopy(dict(remote))
    if "schema_version" not in raw:
        validate_v1(raw)
        raw = migrate_v1(raw)
    validate_v2(raw)
    return raw


def apply_delta(
    remote: Mapping[str, Any],
    delta: StateDelta | Mapping[str, Any],
    *,
    limits: StateLimits = StateLimits(),
) -> dict[str, Any]:
    """Apply a delta without mutating the supplied remote snapshot."""
    if not isinstance(delta, StateDelta):
        delta = StateDelta.from_dict(delta)
    merged = _remote_to_v2(remote)

    _apply_upserts(merged["sources"], delta.source_upserts, source=True)
    _apply_upserts(merged["candidates"], delta.candidate_upserts)
    for candidate_id, tombstone in delta.candidate_removals.items():
        current = merged["candidates"].get(candidate_id)
        if current is not None and _tombstone_wins(current, tombstone["removed_at"]):
            del merged["candidates"][candidate_id]

    for run_id, frozen in delta.run_upserts.items():
        incoming = _thaw(frozen)
        current = merged["runs"].get(run_id)
        merged["runs"][run_id] = (
            incoming if current is None else _merge_run(current, incoming)
        )
    for run_id, tombstone in delta.run_removals.items():
        current = merged["runs"].get(run_id)
        if current is not None and _tombstone_wins(current, tombstone["removed_at"]):
            del merged["runs"][run_id]

    queues = _queue_maps(merged)
    _apply_upserts(queues["immediate"], delta.pending_immediate_upserts)
    _apply_upserts(queues["moderate"], delta.pending_moderate_upserts)
    for tombstone in delta.pending_removals.values():
        queue = queues[tombstone["queue_kind"]]
        current = queue.get(tombstone["revision_id"])
        if current is not None and _tombstone_wins(current, tombstone["removed_at"]):
            del queue[tombstone["revision_id"]]

    pending_health = merged["digest"]["pending_health_summaries"]
    _apply_upserts(pending_health, delta.pending_health_upserts)
    for delivery_id, tombstone in delta.pending_health_removals.items():
        current = pending_health.get(delivery_id)
        if current is not None and _tombstone_wins(current, tombstone["removed_at"]):
            del pending_health[delivery_id]

    delivered_health = merged["digest"]["delivered_health_summaries"]
    _apply_receipts(delivered_health, delta.delivered_health_upserts, health=True)
    for delivery_id, tombstone in delta.delivered_health_removals.items():
        current = delivered_health.get(delivery_id)
        if current is not None and _tombstone_wins(current, tombstone["removed_at"]):
            del delivered_health[delivery_id]
    for delivery_id in tuple(pending_health):
        if delivery_id in delivered_health:
            del pending_health[delivery_id]

    delivered = merged["delivery"]["delivered"]
    _apply_receipts(delivered, delta.delivered_upserts)
    for revision_id, tombstone in delta.delivered_removals.items():
        current = delivered.get(revision_id)
        if current is not None and _tombstone_wins(current, tombstone["removed_at"]):
            del delivered[revision_id]
    for revision_id in delivered:
        queues["immediate"].pop(revision_id, None)
        queues["moderate"].pop(revision_id, None)

    if delta.meta_upserts:
        merged["meta"] = _choose_record(merged["meta"], _thaw(delta.meta_upserts))
    if delta.digest_completion is not None:
        completion = delta.digest_completion
        set_completion_if_newer(
            merged["digest"],
            date_key="last_processed_date",
            timestamp_key="last_processed_at",
            local_date=completion["local_date"],
            completed_at=completion["completed_at"],
        )
    if delta.health_summary_completion is not None:
        completion = delta.health_summary_completion
        set_completion_if_newer(
            merged["digest"],
            date_key="last_health_summary_date",
            timestamp_key="last_health_summary_at",
            local_date=completion["local_date"],
            completed_at=completion["completed_at"],
        )

    prune_state(merged, parse_utc(delta.created_at), limits)
    validate_v2(merged)
    return merged


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Apply a job tracker state delta")
    parser.add_argument("--state", required=True)
    parser.add_argument("--delta", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--config", default="config.yaml")
    args = parser.parse_args(argv)
    output = Path(args.output)
    try:
        state_path = Path(args.state)
        delta_path = Path(args.delta)
        from .config import build_state_limits, load_config

        limits = build_state_limits(load_config(args.config))
        raw_state = _load_json(state_path)
        delta = StateDelta.from_dict(_load_json(delta_path))
        merged = apply_delta(raw_state, delta, limits=limits)
        changed = (
            "schema_version" not in raw_state
            or canonical_state_hash(raw_state) != canonical_state_hash(merged)
        )
        if changed or output.resolve() != state_path.resolve() or not output.exists():
            atomic_write_json(output, merged)
        return 0
    except (OSError, ValueError, json.JSONDecodeError, RuntimeError):
        print("state merge failed", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
