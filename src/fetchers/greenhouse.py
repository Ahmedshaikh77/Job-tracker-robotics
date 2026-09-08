"""Complete Greenhouse board and official detail adapter."""
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
    not_modified_fetch,
    parse_display_location,
    register,
    source_key,
    strip_html,
    utc_now,
    valid_https_url,
)


@register
class GreenhouseFetcher(Fetcher):
    name = "greenhouse"

    def fetch(self, company: dict, context: FetchContext) -> FetchResult:
        url = (
            f"https://boards-api.greenhouse.io/v1/boards/{company['slug']}"
            "/jobs?content=true"
        )
        etag = None if context.force_full else context.previous_etag
        try:
            response = self.http.get_json(url, etag=etag)
        except Exception:
            return failed_fetch(context, "Greenhouse official board unavailable")
        if response.not_modified:
            return not_modified_fetch(context, response.etag or etag)
        payload = response.data
        if not isinstance(payload, dict):
            return failed_fetch(context, "Greenhouse board schema mismatch")
        meta = payload.get("meta")
        rows = payload.get("jobs")
        total = meta.get("total") if isinstance(meta, dict) else None
        if isinstance(total, bool) or not isinstance(total, int) or total < 0:
            return failed_fetch(context, "Greenhouse board schema mismatch")
        if not isinstance(rows, list):
            return failed_fetch(context, "Greenhouse board schema mismatch")
        if total == 0 and rows == []:
            return empty_valid_fetch(etag=response.etag)
        if len(rows) != total:
            return failed_fetch(
                context,
                "Greenhouse authoritative total mismatch",
                pages_fetched=1,
                partial=True,
            )
        jobs: list[Job] = []
        seen: set[str] = set()
        try:
            for row in rows:
                if not isinstance(row, dict):
                    raise ValueError
                job_id = str(row.get("id", "")).strip()
                title = row.get("title")
                application_url = row.get("absolute_url")
                if (
                    not job_id
                    or not isinstance(title, str)
                    or not title.strip()
                    or not valid_https_url(application_url)
                    or job_id in seen
                ):
                    raise ValueError
                seen.add(job_id)
                raw_location = row.get("location")
                location_name = (
                    raw_location.get("name", "")
                    if isinstance(raw_location, dict)
                    else ""
                )
                location, city, region, country = parse_display_location(location_name)
                description = strip_html(row.get("content"))
                provenance = {
                    "title": FactSource.STRUCTURED_FEED,
                    "url": FactSource.STRUCTURED_FEED,
                }
                if location:
                    provenance["location"] = FactSource.STRUCTURED_FEED
                if description:
                    provenance["description"] = FactSource.STRUCTURED_FEED
                if row.get("updated_at"):
                    provenance["updated_at"] = FactSource.STRUCTURED_FEED
                jobs.append(
                    Job(
                        source_type=self.name,
                        source_key=source_key(company),
                        company=company["name"],
                        job_id=job_id,
                        title=title.strip(),
                        location=location,
                        city=city,
                        region=region,
                        country_code=country,
                        url=application_url,
                        description=description,
                        updated_at=row.get("updated_at") or None,
                        provenance=provenance,
                    )
                )
        except (TypeError, ValueError):
            return failed_fetch(
                context,
                "Greenhouse posting schema mismatch",
                pages_fetched=1,
                partial=True,
            )
        return FetchResult(
            jobs=tuple(jobs),
            active_ids=frozenset(seen),
            complete=True,
            source_total=total,
            pages_fetched=1,
            fetched_at=utc_now(),
            health=FetchHealth.HEALTHY,
            total_is_authoritative=True,
            etag=response.etag,
        )

    def fetch_detail(self, company: dict, job: Job) -> DetailResult:
        url = (
            f"https://boards-api.greenhouse.io/v1/boards/{company['slug']}"
            f"/jobs/{job.job_id}"
        )
        try:
            payload = self.http.get_json(url).data
        except Exception as exc:
            status = http_status(exc)
            return DetailResult(
                job=None,
                status=(
                    DetailStatus.CLOSED
                    if status in {404, 410}
                    else DetailStatus.FAILED
                ),
                fetched_at=utc_now(),
                error=(
                    "Greenhouse official detail closed"
                    if status in {404, 410}
                    else "Greenhouse official detail unavailable"
                ),
            )
        if (
            not isinstance(payload, dict)
            or str(payload.get("id", "")) != job.job_id
            or not isinstance(payload.get("content"), str)
            or not strip_html(payload.get("content"))
        ):
            return DetailResult(
                job=None,
                status=DetailStatus.FAILED,
                fetched_at=utc_now(),
                error="Greenhouse detail schema mismatch",
            )
        provenance = dict(job.provenance)
        provenance["description"] = FactSource.OFFICIAL_DETAIL
        if payload.get("first_published"):
            provenance["posted_at"] = FactSource.OFFICIAL_DETAIL
        if payload.get("updated_at"):
            provenance["updated_at"] = FactSource.OFFICIAL_DETAIL
        enriched = replace(
            job,
            description=strip_html(payload["content"]),
            posted_at=payload.get("first_published") or job.posted_at,
            updated_at=payload.get("updated_at") or job.updated_at,
            provenance=provenance,
        )
        return DetailResult(
            job=enriched,
            status=DetailStatus.HEALTHY,
            fetched_at=utc_now(),
        )
