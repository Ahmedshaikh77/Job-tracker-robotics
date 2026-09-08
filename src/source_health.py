"""Source health classification and circuit probe policy."""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from .models import CircuitState, FetchHealth, FetchResult


@dataclass(frozen=True, slots=True)
class SourceHealthPolicy:
    failure_threshold: int = 3
    probe_interval: timedelta = timedelta(hours=24)
    shrink_ratio: Decimal = Decimal("0.40")
    shrink_min_previous_count: int = 20


def _parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("probe timestamp must be timezone aware")
    return parsed.astimezone(timezone.utc)


def should_probe(
    source: Mapping[str, Any],
    now: datetime,
    policy: SourceHealthPolicy = SourceHealthPolicy(),
) -> bool:
    del policy
    if now.tzinfo is None:
        raise ValueError("now must be timezone aware")
    circuit = CircuitState(source.get("circuit", CircuitState.CLOSED.value))
    if circuit is CircuitState.CLOSED:
        return True
    next_probe_at = source.get("next_probe_at")
    return next_probe_at is None or _parse_timestamp(next_probe_at) <= now


def classify_snapshot(
    previous_active_ids: Collection[str],
    result: FetchResult,
    policy: SourceHealthPolicy = SourceHealthPolicy(),
) -> FetchHealth:
    """Classify a result before accepting any inventory mutation."""
    if not result.complete or result.health in {FetchHealth.PARTIAL, FetchHealth.FAILED}:
        return result.health
    if result.unchanged:
        return result.health
    previous_count = len(previous_active_ids)
    current_count = len(result.active_ids)
    if (
        previous_count >= policy.shrink_min_previous_count
        and Decimal(current_count) / Decimal(previous_count) < policy.shrink_ratio
        and not result.total_is_authoritative
    ):
        return FetchHealth.PARTIAL
    return result.health


def is_unconfirmed_shrink(
    previous_active_ids: Collection[str],
    result: FetchResult,
    policy: SourceHealthPolicy = SourceHealthPolicy(),
) -> bool:
    return (
        result.health not in {FetchHealth.PARTIAL, FetchHealth.FAILED}
        and classify_snapshot(previous_active_ids, result, policy)
        is FetchHealth.PARTIAL
    )
