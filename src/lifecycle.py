"""Candidate lifecycle, revision, and inventory reconciliation rules."""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Protocol

from .dedupe import normalize_official_url, normalize_text
from .models import CircuitState, FetchHealth, Job, JobAssessment, Recommendation


@dataclass(frozen=True, slots=True)
class RevisionPolicy:
    same_id_reopen_days: int = 7
    compensation_material_change_ratio: Decimal = Decimal("0.10")
    compensation_threshold: Decimal = Decimal("100000")


@dataclass(frozen=True, slots=True)
class SourceTransition:
    source_key: str
    previous_health: FetchHealth | None
    health: FetchHealth
    previous_circuit: CircuitState
    circuit: CircuitState
    closed_job_keys: tuple[str, ...]
    warnings: tuple[str, ...]


def material_detail_hash(job: Job) -> str:
    return job.content_hash


def comparison_basis(record: Mapping[str, Any]) -> Mapping[str, Any] | None:
    return record.get("last_alert_basis") or record.get("seed_baseline")


def is_migration_equivalent(
    record: Mapping[str, Any], assessment: JobAssessment
) -> bool:
    snapshot = record.get("migration_snapshot")
    if not isinstance(snapshot, Mapping):
        return False
    job = assessment.job
    return dict(snapshot) == {
        "company": normalize_text(job.company),
        "title": normalize_text(job.title),
        "location": normalize_text(job.location),
        "url": normalize_official_url(job.url),
    }


def max_delivered_generation(
    aliases: Collection[str],
    delivered_receipts: Mapping[str, Mapping[str, Any]],
) -> int:
    durable = set(aliases)
    return max(
        (
            int(receipt["reopen_generation"])
            for receipt in delivered_receipts.values()
            if durable.intersection(receipt.get("identity_aliases", ()))
        ),
        default=-1,
    )


def _recommendation_rank(value: str | Recommendation | None) -> int:
    try:
        recommendation = Recommendation(value) if value is not None else Recommendation.SKIP
    except ValueError:
        return 0
    return {
        Recommendation.SKIP: 0,
        Recommendation.MODERATE: 1,
        Recommendation.STRONG: 2,
        Recommendation.APPLY_NOW: 3,
    }[recommendation]


def _decimal(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    return Decimal(str(value))


def _salary_material(
    old: Mapping[str, Any] | None,
    new: Mapping[str, Any] | None,
    policy: RevisionPolicy,
) -> bool:
    old = old or {}
    new = new or {}
    if old.get("currency") != new.get("currency"):
        return True
    for key in ("minimum", "maximum"):
        previous = _decimal(old.get(key))
        current = _decimal(new.get(key))
        if previous is None or current is None:
            if previous != current:
                return True
            continue
        if previous == 0:
            if current != 0:
                return True
        elif abs(current - previous) / abs(previous) >= policy.compensation_material_change_ratio:
            return True
        crossed = (previous < policy.compensation_threshold <= current) or (
            current < policy.compensation_threshold <= previous
        )
        if crossed:
            return True
    return False


def _assessment_material_snapshot(assessment: JobAssessment) -> dict[str, Any]:
    salary = assessment.compensation.salary
    return {
        "title": assessment.job.title,
        "employment_type": assessment.job.employment_type.value,
        "required_experience": {
            "stated_minimum": assessment.experience.stated_required_minimum,
            "stated_maximum": assessment.experience.stated_required_maximum,
            "effective_minimum": assessment.experience.effective_required_minimum,
            "effective_maximum": assessment.experience.effective_required_maximum,
            "preferred_minimum": assessment.experience.preferred_minimum,
            "preferred_maximum": assessment.experience.preferred_maximum,
            "flexible": assessment.experience.flexible,
            "unresolved": assessment.experience.unresolved,
        },
        "authorization": assessment.authorization.status.value,
        "location": {
            "raw": assessment.job.location,
            "city": assessment.job.city,
            "region": assessment.job.region,
            "country_code": assessment.job.country_code,
        },
        "salary": None
        if salary is None
        else {
            "minimum": str(salary.annual_minimum)
            if salary.annual_minimum is not None
            else None,
            "maximum": str(salary.annual_maximum)
            if salary.annual_maximum is not None
            else None,
            "currency": salary.currency,
        },
    }


def should_alert_revision(
    candidate_record: Mapping[str, Any] | None,
    assessment: JobAssessment,
    now: datetime,
    policy: RevisionPolicy = RevisionPolicy(),
) -> bool:
    if now.tzinfo is None:
        raise ValueError("now must be timezone aware")
    if not assessment.eligible or assessment.recommendation is Recommendation.SKIP:
        return False
    if candidate_record is None:
        return True
    if candidate_record.get("last_queued_revision_id") == assessment.revision_id:
        return False
    invalidated = set(
        (candidate_record.get("last_queue_invalidation") or {}).get(
            "revision_ids", ()
        )
    )
    if assessment.revision_id in invalidated:
        return True
    if candidate_record.get("migration_baseline_pending"):
        return not is_migration_equivalent(candidate_record, assessment)
    basis = comparison_basis(candidate_record)
    if basis is None:
        return not candidate_record.get("discovered_during_seed", False)
    if assessment.reopen_generation > int(basis.get("reopen_generation", 0)):
        return True
    if _recommendation_rank(assessment.recommendation) > _recommendation_rank(
        basis.get("recommendation")
    ):
        return True
    current = _assessment_material_snapshot(assessment)
    for key in (
        "title",
        "employment_type",
        "required_experience",
        "authorization",
        "location",
    ):
        if current[key] != basis.get(key):
            return True
    if _salary_material(basis.get("salary"), current.get("salary"), policy):
        return True
    reopened_at = candidate_record.get("reopened_at")
    last_closed_at = candidate_record.get("last_closed_at")
    if reopened_at and last_closed_at:
        closed = datetime.fromisoformat(last_closed_at.replace("Z", "+00:00"))
        reopened = datetime.fromisoformat(reopened_at.replace("Z", "+00:00"))
        if reopened - closed >= timedelta(days=policy.same_id_reopen_days):
            return True
    return False


class _InvalidationManager(Protocol):
    state: dict[str, Any]

    def invalidate_candidate_queue(self, *args: Any, **kwargs: Any) -> tuple[str, ...]: ...

    def cancel_detail_retry(self, source_key: str, posting_id: str, now: datetime) -> bool: ...


def reconcile_inventory(
    manager: _InvalidationManager,
    source_key: str,
    active_ids: Collection[str],
    now: datetime,
) -> tuple[str, ...]:
    """Apply accepted complete omissions and return newly closed source refs."""
    from .models import QueueInvalidationReason

    active = set(active_ids)
    closed_refs: list[str] = []
    for candidate_id, candidate in manager.state["candidates"].items():
        candidate_changed = False
        for ref_key, reference in candidate.get("source_refs", {}).items():
            if reference.get("source_key") != source_key:
                continue
            posting_id = reference["posting_id"]
            if posting_id in active:
                if reference.get("missing_count", 0) or reference.get("closed_at"):
                    reference["missing_count"] = 0
                    reference["closed_at"] = None
                    reference["record_updated_at"] = now.isoformat()
                    candidate_changed = True
                continue
            if reference.get("closed_at") is not None:
                continue
            missing_count = int(reference.get("missing_count", 0)) + 1
            reference["missing_count"] = missing_count
            reference["record_updated_at"] = now.isoformat()
            candidate_changed = True
            if missing_count >= 2:
                reference["closed_at"] = now.isoformat()
                closed_refs.append(ref_key)
                manager.invalidate_candidate_queue(
                    candidate_id,
                    QueueInvalidationReason.COMPLETE_OMISSION_CLOSED,
                    now,
                    source_key=source_key,
                )
                manager.cancel_detail_retry(source_key, posting_id, now)
        references = candidate.get("source_refs", {}).values()
        if references and all(ref.get("closed_at") is not None for ref in references):
            if candidate.get("closed_at") is None:
                candidate["closed_at"] = now.isoformat()
                candidate["last_closed_at"] = now.isoformat()
                candidate_changed = True
            manager.invalidate_candidate_queue(
                candidate_id,
                QueueInvalidationReason.COMPLETE_OMISSION_CLOSED,
                now,
            )
        if candidate_changed:
            candidate["record_updated_at"] = now.isoformat()
            manager.dirty = True
    return tuple(sorted(closed_refs))
