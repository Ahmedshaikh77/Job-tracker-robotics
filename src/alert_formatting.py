"""Compact alerts with explicit factual provenance and separate tracker judgments."""
from __future__ import annotations

import html
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlsplit

from .models import AlertFact, AlertItem, EvidenceStatus, FactSource, WorkplaceType, CompensationStatus, AuthorizationStatus


@dataclass(frozen=True, slots=True)
class MessageChunk:
    text: str
    revision_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class FormattingQuarantine:
    revision_id: str
    source_key: str
    code: str


@dataclass(frozen=True, slots=True)
class ChunkBuildResult:
    chunks: tuple[MessageChunk, ...]
    quarantines: tuple[FormattingQuarantine, ...]


def html_escape(value):
    return html.escape(str(value or ''), quote=True)


def validate_identity(company, title, url):
    parsed = urlsplit(url)
    if (not company or len(company) > 200 or not title or len(title) > 300
            or len(url) > 2048 or parsed.scheme != 'https' or not parsed.hostname
            or parsed.username is not None or parsed.password is not None):
        raise ValueError('Invalid alert identity or application URL')


def _aware(value):
    stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if stamp.tzinfo is None:
        raise ValueError('Alert timestamps must be timezone-aware')


def project_alert_item(assessment, candidate_state, queued_at, queued_run_id, fetch_completed_at):
    job = assessment.job
    if not assessment.eligible or assessment.authorization.status is AuthorizationStatus.BLOCKED:
        raise ValueError('Ineligible assessments cannot become alerts')
    validate_identity(job.company, job.title, job.url)
    if not queued_run_id:
        raise ValueError('Alert run ID is required')
    for stamp in (queued_at, fetch_completed_at, candidate_state['first_seen_at']):
        _aware(stamp)

    def fact(value, source, known=True):
        status = (EvidenceStatus.NOT_PUBLISHED if not known else EvidenceStatus.CONFIRMED
                  if source in (FactSource.STRUCTURED_FEED, FactSource.OFFICIAL_DETAIL) else EvidenceStatus.UNRESOLVED)
        return AlertFact(value or 'Unknown', status,
                         source if known else FactSource.UNAVAILABLE)

    aliases = candidate_state.get('identity_aliases', candidate_state.get('aliases', ()))
    aliases = tuple(sorted(x for x in aliases if not x.startswith(('url:', 'https://', 'http://'))))
    return AlertItem(
        revision_id=assessment.revision_id, candidate_id=assessment.candidate_id,
        reopen_generation=assessment.reopen_generation, identity_aliases=aliases,
        source_key=job.source_key, company=job.company, title=job.title, application_url=job.url,
        location=fact(job.location, job.provenance.get('location', FactSource.UNAVAILABLE), bool(job.location)),
        work_arrangement=fact(job.workplace_type.value, job.provenance.get('workplace_type', FactSource.UNAVAILABLE),
                              job.workplace_type is not WorkplaceType.UNKNOWN),
        posted_date=fact(job.posted_at, job.provenance.get('posted_at', FactSource.UNAVAILABLE), bool(job.posted_at)),
        first_seen_at=candidate_state['first_seen_at'],
        salary=AlertFact(assessment.compensation.label, EvidenceStatus.UNRESOLVED
                        if assessment.compensation.status is CompensationStatus.UNRESOLVED
                        else EvidenceStatus.CONFIRMED if assessment.compensation.salary is not None
                        else EvidenceStatus.NOT_PUBLISHED, assessment.compensation.source),
        experience=AlertFact(assessment.experience.evidence or 'Unknown', EvidenceStatus.UNRESOLVED
                        if assessment.experience.unresolved else EvidenceStatus.CONFIRMED
                        if assessment.experience.source is not FactSource.UNAVAILABLE else EvidenceStatus.NOT_PUBLISHED,
                        assessment.experience.source),
        authorization=AlertFact(assessment.authorization.evidence or 'Future sponsorship support uncertain',
                                EvidenceStatus.CONFIRMED if assessment.authorization.status is AuthorizationStatus.CONFIRMED_SUPPORT
                                else EvidenceStatus.NOT_PUBLISHED if assessment.authorization.source is FactSource.UNAVAILABLE
                                else EvidenceStatus.UNRESOLVED, assessment.authorization.source),
        full_time=fact(job.employment_type.value, job.provenance.get('employment_type', FactSource.UNAVAILABLE)),
        score=assessment.score, recommendation=assessment.recommendation,
        match_reason=assessment.match_reason, important_gap=assessment.important_gap,
        resume_filename=assessment.resume_filename or 'No matching resume', role_family=assessment.role_family or 'Unknown',
        resume_reason=assessment.resume_reason, queued_at=queued_at, queued_run_id=queued_run_id,
        fetch_completed_at=fetch_completed_at)


def _short(value, maximum):
    value = str(value or '')
    return value if len(value) <= maximum else value[:max(0, maximum - 1)] + '…'


def _fact_line(label, fact, maximum):
    if fact.provenance is FactSource.TRACKER_INFERENCE:
        provenance = 'Tracker inference'
    elif fact.status is EvidenceStatus.NOT_PUBLISHED:
        provenance = fact.status.value
    elif fact.provenance is FactSource.UNAVAILABLE:
        provenance = fact.status.value
    else:
        provenance = f'{fact.status.value}: {fact.provenance.value}'
    return f'{label} [{provenance}]: {html_escape(_short(fact.value, maximum))}'


def _skeleton(item):
    return (f'<b>{html_escape(item.recommendation.value)} | {item.score}/100</b>\n'
            f'<b>{html_escape(item.company)}: {html_escape(item.title)}</b>')


def _link(item):
    return f'<a href="{html_escape(item.application_url)}">Apply on the official posting</a>'


def format_alert_entry(item, *, optional_limit=500, compact=False):
    validate_identity(item.company, item.title, item.application_url)
    cap = min(300, optional_limit)
    lines = [_skeleton(item)]
    if compact:
        return '\n'.join(lines + [_link(item)])
    for label, fact in [('Location', item.location), ('Work arrangement', item.work_arrangement),
                        ('Posted date', item.posted_date)]:
        lines.append(_fact_line(label, fact, cap))
    lines.append(f'First seen: {html_escape(item.first_seen_at[:10])}')
    for label, fact in [('Salary', item.salary), ('Experience', item.experience),
                        ('Authorization', item.authorization), ('Employment', item.full_time)]:
        lines.append(_fact_line(label, fact, cap))
    for label, value, limit in [('Fit', item.match_reason, optional_limit), ('Gap', item.important_gap, optional_limit),
                                 ('CV', item.resume_filename, cap), ('CV choice', item.resume_reason, cap),
                                 ('Role family', item.role_family, cap)]:
        lines.append(f'{label} [Tracker assessment]: {html_escape(_short(value, limit))}')
    lines.append(_link(item))
    return '\n'.join(lines)


def build_message_chunks(items, *, heading, limit=3900):
    prefix = f'<b>{html_escape(heading)}</b>\n\n'
    chunks, quarantines, entries, ids = [], [], [], []
    size = len(prefix)
    for item in items:
        try:
            skeleton = format_alert_entry(item, compact=True)
        except ValueError:
            quarantines.append(FormattingQuarantine(item.revision_id, item.source_key, 'invalid-alert-identity'))
            continue
        if len(prefix) + len(skeleton) > limit:
            quarantines.append(FormattingQuarantine(item.revision_id, item.source_key, 'mandatory-skeleton-too-large'))
            continue
        entry = format_alert_entry(item)
        for cap in (300, 180, 100, 40, 0):
            if len(prefix) + len(entry) <= limit:
                break
            entry = format_alert_entry(item, optional_limit=cap)
        if len(prefix) + len(entry) > limit:
            entry = skeleton
        extra = len(entry) + (2 if entries else 0)
        if entries and size + extra > limit:
            chunks.append(MessageChunk(prefix + '\n\n'.join(entries), tuple(ids)))
            entries, ids, size = [], [], len(prefix)
            extra = len(entry)
        entries.append(entry)
        ids.append(item.revision_id)
        size += extra
    if entries:
        chunks.append(MessageChunk(prefix + '\n\n'.join(entries), tuple(ids)))
    return ChunkBuildResult(tuple(chunks), tuple(quarantines))
