from dataclasses import replace

import pytest

from src.models import (
    AlertFact,
    AlertItem,
    EvidenceStatus,
    ExperienceRequirement,
    FactSource,
    Recommendation,
)
from src.alert_formatting import build_message_chunks, format_alert_entry, validate_identity, project_alert_item


@pytest.fixture
def alert_item():
    fact = AlertFact('Austin, TX', EvidenceStatus.CONFIRMED, FactSource.STRUCTURED_FEED)
    unknown = AlertFact('Unknown', EvidenceStatus.NOT_PUBLISHED, FactSource.UNAVAILABLE)
    return AlertItem('rev-1', 'candidate-1', 0,
                     ('source:amazon:robotics-us:10503213',), 'amazon:robotics-us',
                     'Robotics & Co', 'Hardware <Test> Engineer', 'https://company.test/jobs/123',
                     fact, fact, unknown, '2026-09-07T19:00:00Z', fact,
                     AlertFact('0–3 years', EvidenceStatus.CONFIRMED, FactSource.OFFICIAL_DETAIL),
                     AlertFact('Future sponsorship uncertain', EvidenceStatus.UNRESOLVED, FactSource.TRACKER_INFERENCE),
                     fact, 92, Recommendation.APPLY_NOW, 'Hardware testing and sensor integration',
                     'Production validation', 'Hardware Test Engineer.pdf', 'hardware_test', 'Role-specific hardware evidence',
                     '2026-09-07T20:00:00Z', 'run-123', '2026-09-07T19:59:00Z')


def test_readable_card_keeps_decision_fields_without_internal_provenance(alert_item):
    text = format_alert_entry(alert_item)
    assert 'Apply Now · Tracker fit 92/100' in text
    assert 'Job ID: 10503213' in text
    assert 'Robotics &amp; Co' in text
    assert 'Hardware &lt;Test&gt; Engineer' in text
    assert 'Location: Austin, TX (confirmed)' in text
    assert 'Experience: 0–3 years (confirmed)' in text
    assert 'Sponsorship: Future sponsorship uncertain (tracker inference)' in text
    assert 'Posted: Not published · first seen 2026-09-07' in text
    assert 'Fit: Hardware testing and sensor integration' in text
    assert 'Watch-out: Production validation' in text
    assert 'CV: Hardware Test Engineer.pdf' in text
    assert 'structured-feed' not in text
    assert 'official-detail' not in text
    assert 'Role family' not in text
    assert 'CV choice' not in text


def test_job_id_prefers_requisition_alias_and_escapes_source_fallback(alert_item):
    requisition = replace(
        alert_item,
        identity_aliases=(
            'source:amazon:robotics-us:10503213',
            'req:robotics co:req-7788',
        ),
    )
    source_fallback = replace(
        alert_item,
        identity_aliases=('req:malformed', 'source:amazon:robotics-us:<10503213>'),
    )
    assert 'Job ID: req-7788' in format_alert_entry(requisition)
    assert 'Job ID: &lt;10503213&gt;' in format_alert_entry(source_fallback)


def test_long_legacy_experience_uses_safe_non_numeric_fallback(alert_item):
    legacy = replace(
        alert_item,
        experience=AlertFact(
            'Qualifications: candidates must demonstrate experience with validation, '
            'robotics, manufacturing systems, test planning, root-cause analysis, and '
            'many other responsibilities described in the posting.',
            EvidenceStatus.CONFIRMED,
            FactSource.OFFICIAL_DETAIL,
        ),
    )
    text = format_alert_entry(legacy, optional_limit=40)
    assert 'Experience: See official posting for full experience requirements (confirmed)' in text
    assert 'Qualifications:' not in text


def test_current_roundup_note_is_retained_concisely(alert_item):
    roundup = replace(
        alert_item,
        match_reason=(
            'Current openings roundup (not necessarily newly posted). '
            'Hardware testing and sensor integration'
        ),
    )
    text = format_alert_entry(roundup)
    assert 'Note: Current roundup; not necessarily newly posted.' in text
    assert 'Fit: Hardware testing and sensor integration' in text


def test_work_arrangement_and_employment_remain_visible_in_one_line(alert_item):
    work = replace(
        alert_item,
        work_arrangement=AlertFact(
            'hybrid', EvidenceStatus.CONFIRMED, FactSource.OFFICIAL_DETAIL
        ),
        full_time=AlertFact(
            'full-time', EvidenceStatus.CONFIRMED, FactSource.STRUCTURED_FEED
        ),
    )
    assert 'Work: hybrid (confirmed) · full-time (confirmed)' in format_alert_entry(work)


def test_fit_removes_internal_prefixes_and_deduplicates_labels(alert_item):
    prefixed = replace(
        alert_item,
        match_reason=(
            'skill: Python; domain: Robotics; domain: robotics; '
            'degree: Mechanical Engineering'
        ),
    )
    text = format_alert_entry(prefixed)
    assert 'Fit: Python; Robotics' in text
    assert 'skill:' not in text
    assert 'domain:' not in text
    assert 'degree:' not in text


def test_fit_omits_degree_review_without_claiming_equivalency(alert_item):
    review_group = replace(
        alert_item,
        match_reason=(
            'robotics systems; '
            'degree-review:computer science|technical degree|related experience'
        ),
    )
    text = format_alert_entry(review_group)
    assert 'Fit: robotics systems' in text
    assert 'Degree requirement needs review' not in text
    assert 'computer science' not in text
    assert 'technical degree' not in text
    assert 'related experience' not in text
    assert 'degree-review:' not in text


def test_chunks_preserve_jobs_and_order(alert_item):
    items = [
        replace(
            alert_item,
            revision_id=f'rev-{i}',
            identity_aliases=(f'source:amazon:robotics-us:job-{i}',),
        )
        for i in range(12)
    ]
    result = build_message_chunks(items, heading='Strong matches')
    assert not result.quarantines
    assert all(len(chunk.text) <= 3900 for chunk in result.chunks)
    assert [rid for chunk in result.chunks for rid in chunk.revision_ids] == [x.revision_id for x in items]
    combined = '\n'.join(chunk.text for chunk in result.chunks)
    assert all(f'Job ID: job-{i}' in combined for i in range(12))


def test_oversized_optional_fields_are_shrunk(alert_item):
    huge = replace(alert_item, match_reason='<&' * 10000, important_gap='gap' * 5000,
                   resume_reason='reason' * 3000)
    result = build_message_chunks([huge], heading='Matches')
    assert not result.quarantines
    assert len(result.chunks[0].text) <= 3900
    assert huge.application_url in result.chunks[0].text


def test_mandatory_skeleton_quarantine(alert_item):
    huge = replace(alert_item, application_url='https://x.test/?' + '&' * 2000)
    result = build_message_chunks([huge], heading='Matches')
    assert not result.chunks
    assert result.quarantines[0].code == 'mandatory-skeleton-too-large'


@pytest.mark.parametrize('url', ['http://x.test/', '/relative', 'https://user:pass@x.test', 'javascript:alert(1)'])
def test_invalid_urls_rejected(url):
    with pytest.raises(ValueError):
        validate_identity('Company', 'Engineer', url)


def test_projection_does_not_confirm_unknown_authorization_or_missing_provenance(make_job):
    from tests.test_source_lifecycle import assessment
    from src.models import AuthorizationAssessment, AuthorizationStatus
    evaluated = assessment(make_job(), 'candidate')
    evaluated = replace(evaluated, authorization=AuthorizationAssessment(AuthorizationStatus.UNKNOWN,
                        'No decisive restriction published', FactSource.OFFICIAL_DETAIL))
    item = project_alert_item(evaluated, {'first_seen_at':'2026-09-07T19:00:00Z','aliases':[]},
                              '2026-09-07T20:00:00Z','run-1','2026-09-07T19:59:00Z')
    assert item.authorization.status is EvidenceStatus.UNRESOLVED
    assert item.authorization.value == 'Future sponsorship support not confirmed; ask the recruiter'
    assert item.location.provenance is FactSource.UNAVAILABLE
    assert item.full_time.provenance is FactSource.UNAVAILABLE
    text = format_alert_entry(item)
    assert 'Authorization [Confirmed' not in text
    assert 'Location [Confirmed' not in text
    assert 'Employment [Confirmed' not in text


def test_projection_retains_opt_specific_sponsorship_inference(make_job):
    from tests.test_source_lifecycle import assessment
    from src.models import AuthorizationAssessment, AuthorizationStatus
    evaluated = assessment(make_job(), 'candidate')
    evaluated = replace(
        evaluated,
        authorization=AuthorizationAssessment(
            AuthorizationStatus.OPT_COMPATIBLE_UNCERTAIN,
            'Employer requires work authorization at hire; future sponsorship is unclear',
            FactSource.TRACKER_INFERENCE,
        ),
    )
    item = project_alert_item(
        evaluated,
        {'first_seen_at': '2026-09-07T19:00:00Z', 'aliases': []},
        '2026-09-07T20:00:00Z',
        'run-1',
        '2026-09-07T19:59:00Z',
    )
    text = format_alert_entry(item)
    assert (
        'Sponsorship: Employer requires work authorization at hire; '
        'future sponsorship is unclear (tracker inference)'
    ) in text


@pytest.mark.parametrize('minimum,maximum,period,expected', [
    ('100000', '199999', 'year', 'USD 100,000 to 199,999/year'),
    ('50', '60', 'hour', 'USD 50 to 60/hour'),
    ('120000', None, 'year', 'from USD 120,000/year'),
    (None, '170000', 'year', 'up to USD 170,000/year'),
])
def test_projection_includes_published_pay_range_and_original_period(
        make_job, minimum, maximum, period, expected):
    from decimal import Decimal
    from src.models import SalaryRange, PayPeriod
    from tests.test_source_lifecycle import assessment
    evaluated = assessment(make_job(), 'candidate')
    salary = SalaryRange(Decimal(minimum) if minimum else None, Decimal(maximum) if maximum else None,
                         'USD', PayPeriod(period), FactSource.STRUCTURED_FEED)
    evaluated = replace(evaluated, compensation=replace(evaluated.compensation, salary=salary))
    item = project_alert_item(evaluated, {'first_seen_at': '2026-09-07T19:00:00Z', 'aliases': []},
                              '2026-09-07T20:00:00Z', 'run-1', '2026-09-07T19:59:00Z')
    assert expected in item.salary.value


def test_projection_summarizes_required_range_and_effective_minimum(make_job):
    from tests.test_source_lifecycle import assessment
    evaluated = assessment(make_job(), 'candidate')
    evaluated = replace(
        evaluated,
        experience=ExperienceRequirement(
            4, 6, 2, 6, None, None, True,
            "Master's degree alternative reduces the effective minimum to 2 years",
            False, False,
            'A very long official qualification paragraph that should not be pasted.',
            FactSource.OFFICIAL_DETAIL,
        ),
    )
    item = project_alert_item(
        evaluated,
        {'first_seen_at': '2026-09-07T19:00:00Z', 'aliases': []},
        '2026-09-07T20:00:00Z',
        'run-1',
        '2026-09-07T19:59:00Z',
    )
    assert item.experience.value == 'Required: 4–6 years; effective minimum: 2 years'


def test_projection_does_not_infer_required_years_from_preferred_only(make_job):
    from tests.test_source_lifecycle import assessment
    evaluated = assessment(make_job(), 'candidate')
    evaluated = replace(
        evaluated,
        experience=ExperienceRequirement(
            None, None, None, None, 5, None, False, '', True, False,
            'Five years preferred.', FactSource.OFFICIAL_DETAIL,
        ),
    )
    item = project_alert_item(
        evaluated,
        {'first_seen_at': '2026-09-07T19:00:00Z', 'aliases': []},
        '2026-09-07T20:00:00Z',
        'run-1',
        '2026-09-07T19:59:00Z',
    )
    assert item.experience.value == 'Required years not published; preferred: 5+ years'
