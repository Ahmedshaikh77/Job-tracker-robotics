"""Gem public job-board and official per-post adapter."""
from __future__ import annotations

from dataclasses import replace

from ..models import (
    DetailResult,
    DetailStatus,
    FactSource,
    FetchContext,
    FetchHealth,
    FetchResult,
    Job,
)
from .base import (
    Fetcher,
    empty_valid_fetch,
    failed_fetch,
    http_status,
    normalize_employment_type,
    normalize_workplace_type,
    not_modified_fetch,
    parse_display_location,
    register,
    source_key,
    strip_html,
    utc_now,
    valid_https_url,
)


def _parse_job(company: dict, row: dict) -> Job:
    job_id = str(row.get("id", "")).strip()
    title = row.get("title")
    description = row.get("content_plain") or strip_html(row.get("content"))
    application_url = row.get("absolute_url")
    location_value = row.get("location")
    location_name = (
        location_value.get("name", "") if isinstance(location_value, dict) else ""
    )
    if (
        not job_id
        or not isinstance(title, str)
        or not title.strip()
        or not isinstance(description, str)
        or not description.strip()
        or not valid_https_url(application_url)
    ):
        raise ValueError("Gem posting schema mismatch")
    location, city, region, country = parse_display_location(location_name)
    employment = normalize_employment_type(row.get("employment_type"))
    workplace = normalize_workplace_type(row.get("location_type"))
    provenance = {
        "title": FactSource.STRUCTURED_FEED,
        "url": FactSource.STRUCTURED_FEED,
        "description": FactSource.STRUCTURED_FEED,
    }
    if location:
        provenance["location"] = FactSource.STRUCTURED_FEED
    if employment.value != "unknown":
        provenance["employment_type"] = FactSource.STRUCTURED_FEED
    if workplace.value != "unknown":
        provenance["workplace_type"] = FactSource.STRUCTURED_FEED
    if row.get("first_published_at"):
        provenance["posted_at"] = FactSource.STRUCTURED_FEED
    if row.get("updated_at"):
        provenance["updated_at"] = FactSource.STRUCTURED_FEED
    return Job(
        source_type="gem",
        source_key=source_key(company),
        company=company["name"],
        job_id=job_id,
        requisition_id=str(row.get("internal_job_id") or ""),
        title=title.strip(),
        location=location,
        city=city,
        region=region,
        country_code=country,
        url=application_url,
        description=description.strip(),
        employment_type=employment,
        workplace_type=workplace,
        posted_at=row.get("first_published_at") or None,
        updated_at=row.get("updated_at") or None,
        metadata={
            "created_at": row.get("created_at"),
            "departments": row.get("departments") or [],
            "offices": row.get("offices") or [],
            "raw_employment_type": row.get("employment_type"),
            "raw_workplace_type": row.get("location_type"),
        },
        provenance=provenance,
    )


@register
class GemFetcher(Fetcher):
    name = "gem"

    def fetch(self, company: dict, context: FetchContext) -> FetchResult:
        endpoint = f"https://api.gem.com/job_board/v0/{company['slug']}/job_posts/"
        etag = None if context.force_full else context.previous_etag
        try:
            response = self.http.get_json(endpoint, etag=etag)
        except Exception:
            return failed_fetch(context, "Gem official board unavailable")
        if response.not_modified:
            return not_modified_fetch(context, response.etag or etag)
        if not isinstance(response.data, list):
            return failed_fetch(context, "Gem board schema mismatch")
        if not response.data:
            return empty_valid_fetch(etag=response.etag)
        jobs: list[Job] = []
        seen: set[str] = set()
        try:
            for row in response.data:
                if not isinstance(row, dict):
                    raise ValueError
                job = _parse_job(company, row)
                if job.job_id in seen:
                    raise ValueError
                seen.add(job.job_id)
                jobs.append(job)
        except (TypeError, ValueError):
            return failed_fetch(
                context,
                "Gem posting schema mismatch",
                pages_fetched=1,
                partial=True,
                jobs=tuple(jobs),
            )
        return FetchResult(
            jobs=tuple(jobs),
            active_ids=frozenset(seen),
            complete=True,
            source_total=len(jobs),
            pages_fetched=1,
            fetched_at=utc_now(),
            health=FetchHealth.HEALTHY,
            total_is_authoritative=False,
            etag=response.etag,
        )

    def fetch_detail(self, company: dict, job: Job) -> DetailResult:
        endpoint = (
            f"https://api.gem.com/job_board/v0/{company['slug']}"
            f"/job_posts/{job.job_id}/"
        )
        try:
            payload = self.http.get_json(endpoint).data
        except Exception as exc:
            status = http_status(exc)
            return DetailResult(
                job=None,
                status=(DetailStatus.CLOSED if status in {404, 410} else DetailStatus.FAILED),
                fetched_at=utc_now(),
                error=(
                    "Gem official detail closed"
                    if status in {404, 410}
                    else "Gem official detail unavailable"
                ),
            )
        if not isinstance(payload, dict) or str(payload.get("id", "")) != job.job_id:
            return DetailResult(
                job=None,
                status=DetailStatus.FAILED,
                fetched_at=utc_now(),
                error="Gem detail schema mismatch",
            )
        try:
            parsed = _parse_job(company, payload)
        except (TypeError, ValueError):
            return DetailResult(
                job=None,
                status=DetailStatus.FAILED,
                fetched_at=utc_now(),
                error="Gem detail schema mismatch",
            )
        provenance = dict(parsed.provenance)
        provenance["description"] = FactSource.OFFICIAL_DETAIL
        provenance["url"] = FactSource.OFFICIAL_DETAIL
        return DetailResult(
            job=replace(parsed, provenance=provenance),
            status=DetailStatus.HEALTHY,
            fetched_at=utc_now(),
        )
