from src.filters import JobFilter


def test_legacy_filter_delegates_to_stage_one(make_job):
    passes, score, breakdown = JobFilter({}).passes(make_job())
    assert passes is True
    assert score == 1
    assert breakdown["stage_one_status"] == "enrich"


def test_legacy_filter_rejects_non_target_title(make_job):
    passes, score, breakdown = JobFilter({}).passes(
        make_job(title="Accounts Payable Specialist")
    )
    assert passes is False
    assert score == 0
    assert breakdown["stage_one_status"] == "reject"
