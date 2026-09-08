"""Complete SmartRecruiters board and official detail adapter."""
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
    normalize_country_code,
    normalize_employment_type,
    register,
    source_key,
    strip_html,
    utc_now,
    valid_https_url,
)


def _location(value: object) -> tuple[str, str, str, str]:
    if not isinstance(value, dict):
        return "", "", "", ""
    city = value.get("city") if isinstance(value.get("city"), str) else ""
    region = value.get("region") if isinstance(value.get("region"), str) else ""
    country = normalize_country_code(value.get("country"))
    display = ", ".join(part for part in (city, region, country) if part)
    return display, city, region, country


@register
class SmartRecruitersFetcher(Fetcher):
    name = "smartrecruiters"
    PAGE_SIZE = 100

    def fetch(self, company: dict, context: FetchContext) -> FetchResult:
        endpoint = (
            f"https://api.smartrecruiters.com/v1/companies/{company['slug']}"
            "/postings"
        )
        offset = 0
        pages = 0
        expected_total: int | None = None
        jobs: list[Job] = []
        seen: set[str] = set()
        page_signatures: set[tuple[str, ...]] = set()
        while True:
            try:
                page = self.http.get_json(
                    endpoint,
                    params={
                        "destination": "PUBLIC",
                        "limit": self.PAGE_SIZE,
                        "offset": offset,
                    },
                ).data
            except Exception:
                return failed_fetch(
                    context,
                    "SmartRecruiters official board unavailable",
                    pages_fetched=pages,
                    partial=bool(pages),
                    jobs=tuple(jobs),
                )
            pages += 1
            if not isinstance(page, dict):
                return failed_fetch(
                    context,
                    "SmartRecruiters board schema mismatch",
                    pages_fetched=pages,
                    partial=bool(jobs),
                    jobs=tuple(jobs),
                )
            total = page.get("totalFound")
            rows = page.get("content")
            if (
                isinstance(total, bool)
                or not isinstance(total, int)
                or total < 0
                or not isinstance(rows, list)
            ):
                return failed_fetch(
                    context,
                    "SmartRecruiters board schema mismatch",
                    pages_fetched=pages,
                    partial=bool(jobs),
                    jobs=tuple(jobs),
                )
            if expected_total is None:
                expected_total = total
            elif expected_total != total:
                return failed_fetch(
                    context,
                    "SmartRecruiters total changed during pagination",
                    pages_fetched=pages,
                    partial=True,
                    jobs=tuple(jobs),
                )
            if total == 0 and rows == [] and pages == 1:
                return empty_valid_fetch()
            if not rows and offset < total:
                return failed_fetch(
                    context,
                    "SmartRecruiters pagination ended before total",
                    pages_fetched=pages,
                    partial=bool(jobs),
                    jobs=tuple(jobs),
                )
            ids = tuple(
                str(row.get("id", "")) if isinstance(row, dict) else ""
                for row in rows
            )
            if rows and ids in page_signatures:
                return failed_fetch(
                    context,
                    "SmartRecruiters repeated page",
                    pages_fetched=pages,
                    partial=True,
                    jobs=tuple(jobs),
                )
            page_signatures.add(ids)
            try:
                for row in rows:
                    if not isinstance(row, dict):
                        raise ValueError
                    job_id = str(row.get("id", "")).strip()
                    title = row.get("name")
                    expected_ref = f"{endpoint}/{job_id}"
                    if (
                        not job_id
                        or job_id in seen
                        or not isinstance(title, str)
                        or not title.strip()
                        or row.get("ref") != expected_ref
                    ):
                        raise ValueError
                    seen.add(job_id)
                    display, city, region, country = _location(row.get("location"))
                    employment_value = row.get("typeOfEmployment")
                    employment_raw = (
                        employment_value.get("id")
                        if isinstance(employment_value, dict)
                        else employment_value
                    )
                    employment = normalize_employment_type(employment_raw)
                    provenance = {
                        "title": FactSource.STRUCTURED_FEED,
                        "url": FactSource.STRUCTURED_FEED,
                    }
                    if display:
                        provenance["location"] = FactSource.STRUCTURED_FEED
                    if row.get("releasedDate"):
                        provenance["posted_at"] = FactSource.STRUCTURED_FEED
                    if employment.value != "unknown":
                        provenance["employment_type"] = FactSource.STRUCTURED_FEED
                    jobs.append(
                        Job(
                            source_type=self.name,
                            source_key=source_key(company),
                            company=company["name"],
                            job_id=job_id,
                            title=title.strip(),
                            location=display,
                            city=city,
                            region=region,
                            country_code=country,
                            url=expected_ref,
                            employment_type=employment,
                            posted_at=row.get("releasedDate") or None,
                            metadata={
                                "detail_url": expected_ref,
                                "raw_employment_type": employment_value,
                                "experience_level": row.get("experienceLevel"),
                            },
                            provenance=provenance,
                        )
                    )
            except (TypeError, ValueError):
                return failed_fetch(
                    context,
                    "SmartRecruiters posting schema mismatch",
                    pages_fetched=pages,
                    partial=True,
                    jobs=tuple(jobs),
                )
            offset += len(rows)
            if offset >= total:
                break
            if len(rows) < self.PAGE_SIZE:
                return failed_fetch(
                    context,
                    "SmartRecruiters short page before total",
                    pages_fetched=pages,
                    partial=True,
                    jobs=tuple(jobs),
                )
        if expected_total is None or len(seen) != expected_total:
            return failed_fetch(
                context,
                "SmartRecruiters final count mismatch",
                pages_fetched=pages,
                partial=True,
                jobs=tuple(jobs),
            )
        return FetchResult(
            jobs=tuple(jobs),
            active_ids=frozenset(seen),
            complete=True,
            source_total=expected_total,
            pages_fetched=pages,
            fetched_at=utc_now(),
            health=FetchHealth.HEALTHY,
            total_is_authoritative=True,
        )

    def fetch_detail(self, company: dict, job: Job) -> DetailResult:
        endpoint = (
            f"https://api.smartrecruiters.com/v1/companies/{company['slug']}"
            f"/postings/{job.job_id}"
        )
        if job.metadata.get("detail_url") not in {None, endpoint}:
            return DetailResult(
                job=None,
                status=DetailStatus.FAILED,
                fetched_at=utc_now(),
                error="SmartRecruiters detail reference mismatch",
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
                    "SmartRecruiters official detail closed"
                    if status in {404, 410}
                    else "SmartRecruiters official detail unavailable"
                ),
            )
        if not isinstance(payload, dict) or str(payload.get("id", "")) != job.job_id:
            return DetailResult(
                job=None,
                status=DetailStatus.FAILED,
                fetched_at=utc_now(),
                error="SmartRecruiters detail schema mismatch",
            )
        sections = (
            (payload.get("jobAd") or {}).get("sections")
            if isinstance(payload.get("jobAd"), dict)
            else None
        )
        if not isinstance(sections, dict):
            return DetailResult(
                job=None,
                status=DetailStatus.FAILED,
                fetched_at=utc_now(),
                error="SmartRecruiters detail schema mismatch",
            )
        parts: list[str] = []
        for key in ("jobDescription", "qualifications"):
            section = sections.get(key)
            text = section.get("text") if isinstance(section, dict) else None
            normalized = strip_html(text)
            if normalized:
                parts.append(normalized)
        application_url = payload.get("applyUrl") or payload.get("postingUrl")
        if not parts or not valid_https_url(application_url):
            return DetailResult(
                job=None,
                status=DetailStatus.FAILED,
                fetched_at=utc_now(),
                error="SmartRecruiters detail schema mismatch",
            )
        display, city, region, country = _location(payload.get("location"))
        employment_value = payload.get("typeOfEmployment")
        employment_raw = (
            employment_value.get("id")
            if isinstance(employment_value, dict)
            else employment_value
        )
        provenance = dict(job.provenance)
        provenance["description"] = FactSource.OFFICIAL_DETAIL
        provenance["url"] = FactSource.OFFICIAL_DETAIL
        if display:
            provenance["location"] = FactSource.OFFICIAL_DETAIL
        return DetailResult(
            job=replace(
                job,
                description="\n\n".join(parts),
                url=application_url,
                location=display or job.location,
                city=city or job.city,
                region=region or job.region,
                country_code=country or job.country_code,
                employment_type=normalize_employment_type(employment_raw),
                posted_at=payload.get("releasedDate") or job.posted_at,
                provenance=provenance,
            ),
            status=DetailStatus.HEALTHY,
            fetched_at=utc_now(),
        )
