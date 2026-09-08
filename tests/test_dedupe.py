from src.dedupe import (
    candidate_aliases,
    canonical_job_key,
    durable_identity_aliases,
    normalize_official_url,
    probable_duplicate_key,
    resolve_candidate_id,
)
from src.models import FactSource


def test_normalized_official_url_is_canonical_across_sources(make_job):
    first = make_job(url="HTTPS://Company.TEST/jobs/123/?utm_source=greenhouse&keep=1#apply")
    second = make_job(
        source_type="custom",
        source_key="custom:company",
        url="https://company.test/jobs/123?keep=1",
    )
    assert normalize_official_url(first.url) == "https://company.test/jobs/123?keep=1"
    assert canonical_job_key(first) == canonical_job_key(second)


def test_title_location_fallback_is_probable_not_canonical(make_job):
    first = make_job(job_id="1", url="https://example.test/jobs/1", title="Robotics Test Engineer", location="Austin, TX")
    second = make_job(job_id="2", url="https://example.test/jobs/2", title="Robotics  Test Engineer", location="Austin, Texas")
    assert canonical_job_key(first) != canonical_job_key(second)
    assert probable_duplicate_key(first) == probable_duplicate_key(second)


def test_requisition_alias_requires_employer_provenance(make_job):
    untrusted = make_job(requisition_id="REQ-1")
    trusted = make_job(
        requisition_id="REQ-1",
        provenance={"requisition_id": FactSource.STRUCTURED_FEED},
    )
    assert not any(alias.startswith("req:") for alias in candidate_aliases(untrusted))
    assert any(alias.startswith("req:") for alias in candidate_aliases(trusted))
    assert not any(alias.startswith("url:") for alias in durable_identity_aliases(trusted))


def test_url_merges_only_active_candidate_and_local_ids_stay_scoped(make_job):
    first = make_job()
    candidate_id = canonical_job_key(first)
    active = {candidate_id: {"aliases": list(candidate_aliases(first)), "closed_at": None}}
    cross_source = make_job(source_type="ashby", source_key="ashby:figure", job_id="999")
    assert resolve_candidate_id(cross_source, active) == candidate_id
    active[candidate_id]["closed_at"] = "2026-09-01T00:00:00+00:00"
    assert resolve_candidate_id(cross_source, active) != candidate_id
    different_url = make_job(
        source_type="ashby", source_key="ashby:figure", url="https://other.test/jobs/123"
    )
    assert resolve_candidate_id(different_url, active) != candidate_id
