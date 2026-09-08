from dataclasses import replace

import pytest

from src.models import AlertFact, AlertItem, EvidenceStatus, FactSource, Recommendation
from src.alert_formatting import build_message_chunks, format_alert_entry, validate_identity


@pytest.fixture
def alert_item():
    fact = AlertFact('Austin, TX', EvidenceStatus.CONFIRMED, FactSource.STRUCTURED_FEED)
    unknown = AlertFact('Unknown', EvidenceStatus.NOT_PUBLISHED, FactSource.UNAVAILABLE)
    return AlertItem('rev-1', 'candidate-1', 0, ('source:test:1',), 'ashby:test',
                     'Robotics & Co', 'Hardware <Test> Engineer', 'https://company.test/jobs/123',
                     fact, fact, unknown, '2026-09-07T19:00:00Z', fact,
                     AlertFact('0–3 years', EvidenceStatus.CONFIRMED, FactSource.OFFICIAL_DETAIL),
                     AlertFact('Future sponsorship uncertain', EvidenceStatus.UNRESOLVED, FactSource.TRACKER_INFERENCE),
                     fact, 92, Recommendation.APPLY_NOW, 'Hardware testing and sensor integration',
                     'Production validation', 'Hardware Test Engineer.pdf', 'hardware_test', 'Role-specific hardware evidence',
                     '2026-09-07T20:00:00Z', 'run-123', '2026-09-07T19:59:00Z')


def test_provenance_and_escaping(alert_item):
    text = format_alert_entry(alert_item)
    assert 'Apply Now | 92/100' in text
    assert 'Robotics &amp; Co' in text
    assert 'Hardware &lt;Test&gt; Engineer' in text
    assert 'Salary [Confirmed: structured-feed]' in text
    assert 'Experience [Confirmed: official-detail]' in text
    assert 'Authorization [Tracker inference]' in text
    assert 'Posted date [Not published]: Unknown' in text
    assert 'First seen: 2026-09-07' in text
    assert 'CV [Tracker assessment]' in text


def test_chunks_preserve_jobs_and_order(alert_item):
    items = [replace(alert_item, revision_id=f'rev-{i}') for i in range(12)]
    result = build_message_chunks(items, heading='Strong matches')
    assert not result.quarantines
    assert all(len(chunk.text) <= 3900 for chunk in result.chunks)
    assert [rid for chunk in result.chunks for rid in chunk.revision_ids] == [x.revision_id for x in items]


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
