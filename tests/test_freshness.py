from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from src.freshness import MaterialRevision, assess_freshness, assess_material_revision
from src.lifecycle import RevisionPolicy
from src.models import (
    AuthorizationAssessment,
    AuthorizationStatus,
    CompensationAssessment,
    CompensationStatus,
    ExperienceRequirement,
    FactSource,
    FreshnessStatus,
    PayPeriod,
    SalaryRange,
)
from src.state import StateManager


NOW = datetime(2026, 9, 7, 20, 0, tzinfo=ZoneInfo("America/New_York"))


def _experience(minimum=2):
    return ExperienceRequirement(
        minimum,
        None,
        minimum,
        None,
        None,
        None,
        False,
        "",
        False,
        True,
        f"{minimum} years",
        FactSource.OFFICIAL_DETAIL,
    )


def _authorization(status=AuthorizationStatus.UNKNOWN):
    return AuthorizationAssessment(status, status.value, FactSource.OFFICIAL_DETAIL)


def _compensation(minimum="100000", maximum="120000"):
    salary = SalaryRange(
        Decimal(minimum),
        Decimal(maximum),
        "USD",
        PayPeriod.YEAR,
        FactSource.STRUCTURED_FEED,
    )
    return CompensationAssessment(
        CompensationStatus.CONFIRMED_TARGET,
        "confirmed $100k+",
        10,
        salary,
        "published salary",
        FactSource.STRUCTURED_FEED,
    )


def _observed(manager, job, *, seed=False, now=NOW):
    candidate_id, _, record = manager.observe_candidate(
        job,
        now.astimezone(ZoneInfo("UTC")),
        discovered_during_seed=seed,
    )
    return candidate_id, record


@pytest.mark.parametrize(
    ("posted_at", "status", "eligible", "age_days"),
    [
        ("2026-08-08", FreshnessStatus.RECENT, True, 30),
        ("2026-08-07", FreshnessStatus.STALE, False, 31),
    ],
)
def test_authoritative_posted_date_uses_inclusive_new_york_calendar_window(
    tmp_path, make_job, posted_at, status, eligible, age_days
):
    manager = StateManager.load(tmp_path / "state.json")
    job = make_job(
        posted_at=posted_at,
        provenance={"posted_at": FactSource.OFFICIAL_DETAIL},
    )
    _, record = _observed(manager, job)
    result = assess_freshness(
        job,
        record,
        material_revision=MaterialRevision((), None),
        now=NOW,
    )
    assert (result.status, result.eligible, result.age_days) == (
        status,
        eligible,
        age_days,
    )


def test_unknown_date_is_eligible_only_when_discovered_after_seed(tmp_path, make_job):
    live = StateManager.load(tmp_path / "live.json")
    seeded = StateManager.load(tmp_path / "seeded.json")
    job = make_job(posted_at=None, provenance={})
    _, live_record = _observed(live, job, seed=False)
    _, seed_record = _observed(seeded, job, seed=True)
    no_revision = MaterialRevision((), None)
    live_result = assess_freshness(job, live_record, material_revision=no_revision, now=NOW)
    seed_result = assess_freshness(job, seed_record, material_revision=no_revision, now=NOW)
    assert live_result.status is FreshnessStatus.UNKNOWN_DATE_POST_SEED
    assert live_result.eligible is True
    assert seed_result.status is FreshnessStatus.STALE
    assert seed_result.eligible is False


def test_updated_at_never_substitutes_for_posted_at(tmp_path, make_job):
    manager = StateManager.load(tmp_path / "state.json")
    job = make_job(
        posted_at=None,
        updated_at="2026-09-07",
        provenance={"updated_at": FactSource.STRUCTURED_FEED},
    )
    _, record = _observed(manager, job, seed=True)
    result = assess_freshness(
        job,
        record,
        material_revision=MaterialRevision((), None),
        now=NOW,
    )
    assert result.status is FreshnessStatus.STALE
    assert result.eligible is False


def test_material_official_revision_makes_stale_posting_eligible(tmp_path, make_job):
    manager = StateManager.load(tmp_path / "state.json")
    old_job = make_job(
        title="Robotics Test Engineer",
        posted_at="2026-01-01",
        provenance={"posted_at": FactSource.OFFICIAL_DETAIL},
    )
    candidate_id, record = _observed(manager, old_job, seed=True)
    manager.state["candidates"][candidate_id]["seed_baseline"] = _basis(old_job)
    new_job = make_job(
        title="Hardware Validation Engineer",
        posted_at="2026-01-01",
        provenance={"posted_at": FactSource.OFFICIAL_DETAIL},
    )
    _, _, current = manager.observe_candidate(
        new_job,
        NOW.astimezone(ZoneInfo("UTC")),
        discovered_during_seed=False,
    )
    revision = assess_material_revision(
        new_job,
        _experience(),
        _authorization(),
        _compensation(),
        current,
        policy=RevisionPolicy(),
    )
    result = assess_freshness(new_job, current, material_revision=revision, now=NOW)
    assert revision.changed_fields == ("title",)
    assert result.status is FreshnessStatus.MATERIAL_REVISION
    assert result.eligible is True


def test_description_only_edit_is_not_a_material_revision(tmp_path, make_job):
    manager = StateManager.load(tmp_path / "state.json")
    job = make_job(posted_at="2026-01-01")
    candidate_id, _ = _observed(manager, job, seed=True)
    manager.state["candidates"][candidate_id]["seed_baseline"] = _basis(job)
    edited = make_job(posted_at="2026-01-01", description="New prose, same facts.")
    _, _, current = manager.observe_candidate(
        edited,
        NOW.astimezone(ZoneInfo("UTC")),
        discovered_during_seed=False,
    )
    revision = assess_material_revision(
        edited,
        _experience(),
        _authorization(),
        _compensation(),
        current,
        policy=RevisionPolicy(),
    )
    assert revision.is_material is False
    assert assess_freshness(
        edited, current, material_revision=revision, now=NOW
    ).eligible is False


def test_material_comparison_uses_last_alert_basis_not_latest_evaluation(
    tmp_path, make_job
):
    manager = StateManager.load(tmp_path / "state.json")
    job = make_job()
    candidate_id, record = _observed(manager, job, seed=True)
    baseline = _basis(job)
    manager.state["candidates"][candidate_id]["seed_baseline"] = baseline
    manager.state["candidates"][candidate_id]["last_alert_basis"] = baseline
    manager.state["candidates"][candidate_id]["last_evaluation"] = {
        **baseline,
        "title": "A transient different title",
    }
    revision = assess_material_revision(
        job,
        _experience(),
        _authorization(),
        _compensation(),
        manager.candidate(candidate_id),
        policy=RevisionPolicy(),
    )
    assert revision.changed_fields == ()


@pytest.mark.parametrize(
    ("policy", "old_bounds", "new_bounds"),
    [
        (RevisionPolicy(compensation_material_change_ratio=Decimal("0.04")), ("100000", "120000"), ("105000", "120000")),
        (RevisionPolicy(compensation_threshold=Decimal("95000")), ("90000", "94000"), ("90000", "96000")),
    ],
)
def test_nondefault_revision_policy_controls_compensation_materiality(
    tmp_path, make_job, policy, old_bounds, new_bounds
):
    manager = StateManager.load(tmp_path / "state.json")
    job = make_job()
    candidate_id, _ = _observed(manager, job, seed=True)
    manager.state["candidates"][candidate_id]["seed_baseline"] = _basis(
        job, compensation=_compensation(*old_bounds)
    )
    revision = assess_material_revision(
        job,
        _experience(),
        _authorization(),
        _compensation(*new_bounds),
        manager.candidate(candidate_id),
        policy=policy,
    )
    assert revision.changed_fields == ("compensation",)


def test_freshness_window_is_configurable(tmp_path, make_job):
    manager = StateManager.load(tmp_path / "state.json")
    job = make_job(
        posted_at="2026-08-28",
        provenance={"posted_at": FactSource.OFFICIAL_DETAIL},
    )
    _, record = _observed(manager, job)
    result = assess_freshness(
        job,
        record,
        material_revision=MaterialRevision((), None),
        now=NOW,
        freshness_days=7,
    )
    assert result.status is FreshnessStatus.STALE
    assert result.age_days == 10


def _basis(job, *, compensation=None):
    compensation = compensation or _compensation()
    salary = compensation.salary
    return {
        "title": job.title,
        "employment_type": job.employment_type.value,
        "required_experience": {
            "stated_minimum": 2,
            "stated_maximum": None,
            "effective_minimum": 2,
            "effective_maximum": None,
            "preferred_minimum": None,
            "preferred_maximum": None,
            "flexible": False,
            "unresolved": False,
        },
        "authorization": AuthorizationStatus.UNKNOWN.value,
        "location": {
            "raw": job.location,
            "city": job.city,
            "region": job.region,
            "country_code": job.country_code,
        },
        "salary": {
            "minimum": str(salary.annual_minimum),
            "maximum": str(salary.annual_maximum),
            "currency": salary.currency,
        },
    }
