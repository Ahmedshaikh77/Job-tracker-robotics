from decimal import Decimal

import pytest

from src.models import (
    AlertItem,
    AuthorizationAssessment,
    AuthorizationStatus,
    CompensationAssessment,
    CompensationStatus,
    DetailResult,
    DetailStatus,
    EmploymentType,
    ExperienceRequirement,
    FactSource,
    FetchHealth,
    FetchResult,
    FreshnessAssessment,
    FreshnessStatus,
    Job,
    JobAssessment,
    PayPeriod,
    QualificationAssessment,
    Recommendation,
    SalaryRange,
    ScoreBreakdown,
    job_from_dict,
    job_to_dict,
)


def make_job(**overrides):
    values = {
        "source_type": "greenhouse",
        "source_key": "greenhouse:figureai",
        "company": "Figure",
        "job_id": "123",
        "title": "Robotics Test Engineer",
        "location": "Sunnyvale, CA",
        "url": "https://example.test/jobs/123",
        "description": "Build validation fixtures.",
        "employment_type": EmploymentType.FULL_TIME,
        "country_code": "US",
    }
    values.update(overrides)
    return Job(**values)


def make_assessment(**overrides):
    score = ScoreBreakdown(1, 25, 15, 15, 10, 10, 10, 10, 5)
    values = {
        "job": make_job(),
        "candidate_id": "source:greenhouse:figureai:123",
        "reopen_generation": 0,
        "eligible": True,
        "score": 100,
        "recommendation": Recommendation.APPLY_NOW,
        "role_family": "robotics_test_validation",
        "match_reason": "hardware validation",
        "important_gap": "none",
        "resume_filename": "resume.pdf",
        "resume_reason": "robotics test",
        "experience": ExperienceRequirement(1, None, 1, None, None, None, False, "", False, True, "1 year", FactSource.OFFICIAL_DETAIL),
        "authorization": AuthorizationAssessment(AuthorizationStatus.CONFIRMED_SUPPORT, "sponsorship offered", FactSource.OFFICIAL_DETAIL),
        "freshness": FreshnessAssessment(FreshnessStatus.RECENT, True, 2, "posted", FactSource.OFFICIAL_DETAIL),
        "compensation": CompensationAssessment(CompensationStatus.CONFIRMED_TARGET, "confirmed $100k+", 10, None, "salary", FactSource.OFFICIAL_DETAIL),
        "qualification": QualificationAssessment((), (), (), (), 5),
        "score_breakdown": score,
    }
    values.update(overrides)
    return JobAssessment(**values)


def test_job_key_is_source_scoped_and_hash_tracks_content():
    original = make_job()
    assert original.key == "greenhouse:figureai:123"
    assert original.content_hash == make_job().content_hash
    assert original.content_hash != make_job(description="Automate fixtures").content_hash
    assert original.key != make_job(source_type="ashby", source_key="ashby:figureai").key


def test_job_requires_complete_source_key():
    with pytest.raises(ValueError, match="source_key"):
        make_job(source_key="figureai")


def test_job_deep_freezes_input_and_round_trips_json():
    metadata = {"nested": {"rows": [1, 2]}, "tags": {"b", "a"}}
    provenance = {"description": FactSource.OFFICIAL_DETAIL}
    job = make_job(
        metadata=metadata,
        provenance=provenance,
        salary=SalaryRange(Decimal("50.25"), Decimal("60"), "USD", PayPeriod.HOUR, FactSource.STRUCTURED_FEED),
    )
    metadata["nested"]["rows"].append(3)
    provenance["description"] = FactSource.UNAVAILABLE
    assert job.metadata["nested"]["rows"] == (1, 2)
    assert job.provenance["description"] is FactSource.OFFICIAL_DETAIL
    restored = job_from_dict(job_to_dict(job))
    assert job_to_dict(restored) == job_to_dict(job)
    assert restored.content_hash == job.content_hash


def test_hash_is_independent_of_provenance_insertion_order():
    first = make_job(provenance={"title": FactSource.STRUCTURED_FEED, "description": FactSource.OFFICIAL_DETAIL})
    second = make_job(provenance={"description": FactSource.OFFICIAL_DETAIL, "title": FactSource.STRUCTURED_FEED})
    assert first.content_hash == second.content_hash


def test_job_deserializer_rejects_unknown_and_bad_salary_keys():
    payload = job_to_dict(make_job())
    with pytest.raises(ValueError, match="invalid Job keys"):
        job_from_dict({**payload, "extra": True})
    payload["salary"] = {"minimum": "1"}
    with pytest.raises(ValueError, match="SalaryRange"):
        job_from_dict(payload)


def test_hourly_salary_annualizes_and_unknown_period_does_not():
    hourly = SalaryRange(Decimal("50"), Decimal("60"), "USD", PayPeriod.HOUR)
    assert hourly.annual_minimum == Decimal("104000")
    assert hourly.annual_maximum == Decimal("124800")
    assert SalaryRange(minimum=Decimal("120000")).annual_minimum is None


@pytest.mark.parametrize(
    "kwargs, message",
    [
        ({"jobs": (make_job(),), "active_ids": frozenset()}, "active_ids"),
        ({"jobs": (), "active_ids": frozenset(), "source_total": None}, "empty-valid"),
        ({"complete": False, "health": FetchHealth.HEALTHY}, "complete"),
        ({"complete": True, "health": FetchHealth.FAILED}, "cannot be complete"),
        ({"total_is_authoritative": True, "source_total": None}, "authoritative total"),
        ({"pages_fetched": -1}, "pages_fetched"),
    ],
)
def test_fetch_result_rejects_impossible_combinations(kwargs, message):
    values = {
        "jobs": (make_job(),),
        "active_ids": frozenset({"123"}),
        "complete": True,
        "source_total": 1,
        "pages_fetched": 1,
        "fetched_at": "2026-09-07T12:00:00+00:00",
        "health": FetchHealth.HEALTHY,
        "total_is_authoritative": True,
    }
    values.update(kwargs)
    with pytest.raises(ValueError, match=message):
        FetchResult(**values)


def test_explicit_empty_and_unchanged_results_are_valid():
    empty = FetchResult((), frozenset(), True, 0, 1, "2026-09-07T12:00:00Z", FetchHealth.EMPTY_VALID, True)
    unchanged = FetchResult((), frozenset({"123"}), True, 1, 0, "2026-09-07T12:00:00Z", FetchHealth.HEALTHY, True, unchanged=True)
    assert empty.health is FetchHealth.EMPTY_VALID
    assert unchanged.unchanged is True


@pytest.mark.parametrize(
    "status, job",
    [(DetailStatus.HEALTHY, None), (DetailStatus.CLOSED, make_job()), (DetailStatus.FAILED, make_job())],
)
def test_detail_result_rejects_impossible_status_job_pairs(status, job):
    with pytest.raises(ValueError):
        DetailResult(job, status, "2026-09-07T12:00:00Z")


def test_score_breakdown_validates_version_and_component_caps():
    assert ScoreBreakdown(1, 25, 15, 15, 10, 10, 10, 10, 5).total == 100
    with pytest.raises(ValueError, match="version"):
        ScoreBreakdown(2, 0, 0, 0, 0, 0, 0, 0, 0)
    with pytest.raises(ValueError, match="cap"):
        ScoreBreakdown(1, 26, 0, 0, 0, 0, 0, 0, 0)
    with pytest.raises(ValueError, match="cap"):
        ScoreBreakdown(1, -1, 0, 0, 0, 0, 0, 0, 0)


def test_assessment_rejects_inconsistent_score_and_gates():
    with pytest.raises(ValueError, match="score"):
        make_assessment(score=99)
    with pytest.raises(ValueError, match="gates"):
        make_assessment(job=make_job(employment_type=EmploymentType.CONTRACT))
    with pytest.raises(ValueError, match="gates"):
        make_assessment(resume_filename=None)
    with pytest.raises(ValueError, match="Skip"):
        make_assessment(eligible=False)


def test_revision_changes_for_material_basis_but_not_wording_only():
    original = make_assessment()
    changed_tier = make_assessment(recommendation=Recommendation.STRONG)
    wording_only = make_assessment(match_reason="same evidence, different words")
    assert original.revision_id != changed_tier.revision_id
    assert original.revision_id == wording_only.revision_id


def test_alert_item_contract_cannot_store_description():
    assert "description" not in AlertItem.__dataclass_fields__
