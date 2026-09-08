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


def _salary_text(compensation):
    salary = compensation.salary
    if salary is None or (salary.minimum is None and salary.maximum is None):
        return compensation.label
    currency = salary.currency or 'Currency unknown'
    period = salary.period.value if salary.period.value != 'unknown' else 'period unknown'
    if salary.minimum is None:
        published = f'up to {currency} {salary.maximum:,f}/{period}'
    elif salary.maximum is None:
        published = f'from {currency} {salary.minimum:,f}/{period}'
    elif salary.minimum == salary.maximum:
        published = f'{currency} {salary.minimum:,f}/{period}'
    else:
        published = f'{currency} {salary.minimum:,f} to {salary.maximum:,f}/{period}'
    return f'{published}; {compensation.label}'


def _number_text(value):
    return f'{value:g}'


def _year_range(minimum, maximum):
    if minimum is None:
        return f'up to {_number_text(maximum)} years' if maximum is not None else ''
    if maximum is None:
        return f'{_number_text(minimum)}+ years'
    if minimum == maximum:
        return f'{_number_text(minimum)} years'
    return f'{_number_text(minimum)}–{_number_text(maximum)} years'


def _experience_text(requirement):
    stated = _year_range(
        requirement.stated_required_minimum,
        requirement.stated_required_maximum,
    )
    preferred = _year_range(requirement.preferred_minimum, requirement.preferred_maximum)
    parts = [f'Required: {stated}' if stated else 'Required years not published']
    if preferred:
        parts.append(f'preferred: {preferred}')
    effective = requirement.effective_required_minimum
    if (
        effective is not None
        and requirement.stated_required_minimum is not None
        and effective != requirement.stated_required_minimum
    ):
        parts.append(f'effective minimum: {_number_text(effective)} years')
    return '; '.join(parts)


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
        salary=AlertFact(_salary_text(assessment.compensation), EvidenceStatus.UNRESOLVED
                        if assessment.compensation.status is CompensationStatus.UNRESOLVED
                        else EvidenceStatus.CONFIRMED if assessment.compensation.salary is not None
                        else EvidenceStatus.NOT_PUBLISHED, assessment.compensation.source),
        experience=AlertFact(_experience_text(assessment.experience), EvidenceStatus.UNRESOLVED
                        if assessment.experience.unresolved else EvidenceStatus.CONFIRMED
                        if assessment.experience.source is not FactSource.UNAVAILABLE else EvidenceStatus.NOT_PUBLISHED,
                        assessment.experience.source),
        authorization=AlertFact(
                                'Future sponsorship support not confirmed; ask the recruiter'
                                if assessment.authorization.status is AuthorizationStatus.UNKNOWN
                                else assessment.authorization.evidence or 'Future sponsorship support uncertain',
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
    maximum = max(0, maximum)
    if not maximum:
        return ''
    return value if len(value) <= maximum else value[:maximum - 1] + '…'


def _fact_text(fact, maximum, *, experience=False):
    value = str(fact.value or 'Unknown')
    if experience and len(value) > min(120, max(0, maximum)):
        value = ('See official posting for full experience requirements'
                 if fact.status is EvidenceStatus.CONFIRMED
                 else 'Required experience is unclear; see official posting')
    else:
        value = _short(value, maximum)
    if fact.provenance is FactSource.TRACKER_INFERENCE:
        suffix = 'tracker inference'
    elif fact.status is EvidenceStatus.CONFIRMED:
        suffix = 'confirmed'
    elif fact.status is EvidenceStatus.NOT_PUBLISHED:
        suffix = 'not published'
    elif fact.status is EvidenceStatus.TRACKER_ASSESSMENT:
        suffix = 'tracker assessment'
    else:
        suffix = 'unclear'
    if fact.status is EvidenceStatus.NOT_PUBLISHED and value.casefold() in {
        '', 'unknown', 'not published'
    }:
        return 'Not published'
    return f'{value} ({suffix})'


def _fact_line(label, fact, maximum, *, experience=False):
    return f'{label}: {html_escape(_fact_text(fact, maximum, experience=experience))}'


def _job_id(item):
    for alias in item.identity_aliases:
        parts = alias.split(':', 2)
        if len(parts) == 3 and parts[0] == 'req' and parts[2]:
            return parts[2]
    source_prefix = f'source:{item.source_key}:'
    for alias in item.identity_aliases:
        if alias.startswith(source_prefix) and alias != source_prefix:
            return alias[len(source_prefix):]
    for alias in item.identity_aliases:
        parts = alias.split(':', 2)
        if len(parts) == 3 and parts[0] == 'legacy-local' and parts[2]:
            return parts[2]
    return 'Unavailable'


def _clean_fit(value):
    cleaned = []
    seen = set()
    for part in str(value or '').split(';'):
        part = part.strip()
        prefix, separator, remainder = part.partition(':')
        if separator and prefix.casefold() in {'skill', 'domain', 'degree'}:
            part = remainder.strip()
        key = part.casefold()
        if part and key not in seen:
            cleaned.append(part)
            seen.add(key)
    return '; '.join(cleaned)


def _skeleton(item):
    return (f'<b>{html_escape(item.recommendation.value)} · Tracker fit {item.score}/100</b>\n'
            f'<b>{html_escape(item.title)}</b>\n'
            f'{html_escape(item.company)} · Job ID: {html_escape(_job_id(item))}')


def _link(item):
    return f'<a href="{html_escape(item.application_url)}">Apply on the official posting</a>'


def format_alert_entry(item, *, optional_limit=500, compact=False):
    validate_identity(item.company, item.title, item.application_url)
    cap = min(300, max(0, optional_limit))
    lines = [_skeleton(item)]
    if compact:
        return '\n'.join(lines + [_link(item)])
    lines.append(_fact_line('Location', item.location, cap))
    lines.append(
        f'Work: {html_escape(_fact_text(item.work_arrangement, cap))}'
        f' · {html_escape(_fact_text(item.full_time, cap))}'
    )
    lines.append(_fact_line('Salary', item.salary, cap))
    lines.append(_fact_line('Experience', item.experience, cap, experience=True))
    lines.append(_fact_line('Sponsorship', item.authorization, cap))

    roundup_prefix = 'Current openings roundup (not necessarily newly posted). '
    match_reason = str(item.match_reason or '')
    if match_reason.startswith(roundup_prefix):
        lines.append('Note: Current roundup; not necessarily newly posted.')
        match_reason = match_reason[len(roundup_prefix):]
    lines.append(f'Fit: {html_escape(_short(_clean_fit(match_reason), optional_limit))}')
    lines.append(f'Watch-out: {html_escape(_short(item.important_gap, optional_limit))}')
    lines.append(f'CV: {html_escape(_short(item.resume_filename, cap))}')

    posted = item.posted_date
    if posted.status is EvidenceStatus.CONFIRMED and len(posted.value) >= 10:
        posted = AlertFact(posted.value[:10], posted.status, posted.provenance)
    lines.append(
        f'Posted: {html_escape(_fact_text(posted, cap))}'
        f' · first seen {html_escape(item.first_seen_at[:10])}'
    )
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
