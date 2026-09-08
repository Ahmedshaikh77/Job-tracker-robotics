"""Complete Workday CXS board and official detail adapter."""
from __future__ import annotations

import re
from dataclasses import replace
from urllib.parse import urlsplit

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
    parse_display_location,
    register,
    source_key,
    strip_html,
    utc_now,
    valid_https_url,
)


_REQUISITION_SUFFIX = re.compile(r"_([A-Za-z][A-Za-z0-9-]{1,100})$")


@register
class WorkdayFetcher(Fetcher):
    name = "workday"
    PAGE_SIZE = 20

    def fetch(self, company: dict, context: FetchContext) -> FetchResult:
        endpoint = (
            f"https://{company['host']}/wday/cxs/{company['tenant']}"
            f"/{company['site']}/jobs"
        )
        offset = 0
        pages = 0
        expected_total: int | None = None
        jobs: list[Job] = []
        seen: set[str] = set()
        page_signatures: set[tuple[str, ...]] = set()
        while True:
            payload = {
                "appliedFacets": {},
                "limit": self.PAGE_SIZE,
                "offset": offset,
                "searchText": "",
            }
            try:
                page = self.http.post_json(endpoint, payload=payload).data
            except Exception:
                return failed_fetch(
                    context,
                    "Workday official board unavailable",
                    pages_fetched=pages,
                    partial=bool(pages),
                    jobs=tuple(jobs),
                )
            pages += 1
            if not isinstance(page, dict):
                return failed_fetch(
                    context,
                    "Workday board schema mismatch",
                    pages_fetched=pages,
                    partial=bool(jobs),
                    jobs=tuple(jobs),
                )
            total = page.get("total")
            rows = page.get("jobPostings")
            if (
                isinstance(total, bool)
                or not isinstance(total, int)
                or total < 0
                or not isinstance(rows, list)
            ):
                return failed_fetch(
                    context,
                    "Workday board schema mismatch",
                    pages_fetched=pages,
                    partial=bool(jobs),
                    jobs=tuple(jobs),
                )
            if expected_total is None:
                expected_total = total
            elif total == 0 and rows:
                # Captured from the current Boston Dynamics Workday board on
                # 2026-09-07. Nonfirst pages use zero as a sentinel even while
                # returning rows. The first-page total remains authoritative
                # and the exact final unique count is still required below.
                pass
            elif total != expected_total:
                return failed_fetch(
                    context,
                    "Workday total changed during pagination",
                    pages_fetched=pages,
                    partial=True,
                    jobs=tuple(jobs),
                )
            if total == 0 and rows == [] and pages == 1:
                return empty_valid_fetch()
            if not rows and offset < total:
                return failed_fetch(
                    context,
                    "Workday pagination ended before total",
                    pages_fetched=pages,
                    partial=bool(jobs),
                    jobs=tuple(jobs),
                )
            paths = tuple(
                row.get("externalPath", "") if isinstance(row, dict) else ""
                for row in rows
            )
            if rows and paths in page_signatures:
                return failed_fetch(
                    context,
                    "Workday repeated page",
                    pages_fetched=pages,
                    partial=True,
                    jobs=tuple(jobs),
                )
            page_signatures.add(paths)
            try:
                for row in rows:
                    if not isinstance(row, dict):
                        raise ValueError
                    title = row.get("title")
                    external_path = row.get("externalPath")
                    match = (
                        _REQUISITION_SUFFIX.search(external_path)
                        if isinstance(external_path, str)
                        else None
                    )
                    if (
                        not isinstance(title, str)
                        or not title.strip()
                        or not isinstance(external_path, str)
                        or not external_path.startswith("/")
                        or match is None
                    ):
                        raise ValueError
                    job_id = match.group(1)
                    if job_id in seen:
                        raise ValueError
                    seen.add(job_id)
                    location, city, region, country = parse_display_location(
                        row.get("locationsText")
                    )
                    application_url = (
                        f"https://{company['host']}/en-US/{company['site']}"
                        f"{external_path}"
                    )
                    provenance = {
                        "title": FactSource.STRUCTURED_FEED,
                        "url": FactSource.STRUCTURED_FEED,
                    }
                    if location:
                        provenance["location"] = FactSource.STRUCTURED_FEED
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
                            metadata={
                                "external_path": external_path,
                                "posted_on": row.get("postedOn"),
                            },
                            provenance=provenance,
                        )
                    )
            except (TypeError, ValueError):
                return failed_fetch(
                    context,
                    "Workday posting schema mismatch",
                    pages_fetched=pages,
                    partial=True,
                    jobs=tuple(jobs),
                )
            offset += len(rows)
            if expected_total is not None and offset >= expected_total:
                break
            if len(rows) < self.PAGE_SIZE:
                return failed_fetch(
                    context,
                    "Workday short page before total",
                    pages_fetched=pages,
                    partial=True,
                    jobs=tuple(jobs),
                )
        if expected_total is None or len(seen) != expected_total:
            return failed_fetch(
                context,
                "Workday final count mismatch",
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
        external_path = job.metadata.get("external_path")
        if not isinstance(external_path, str) or not external_path.startswith("/"):
            return DetailResult(
                job=None,
                status=DetailStatus.FAILED,
                fetched_at=utc_now(),
                error="Workday detail reference mismatch",
            )
        endpoint = (
            f"https://{company['host']}/wday/cxs/{company['tenant']}"
            f"/{company['site']}{external_path}"
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
                    "Workday official detail closed"
                    if status in {404, 410}
                    else "Workday official detail unavailable"
                ),
            )
        info = payload.get("jobPostingInfo") if isinstance(payload, dict) else None
        if not isinstance(info, dict):
            return DetailResult(
                job=None,
                status=DetailStatus.FAILED,
                fetched_at=utc_now(),
                error="Workday detail schema mismatch",
            )
        if info.get("canApply") is not True or info.get("posted") is not True:
            return DetailResult(
                job=None,
                status=DetailStatus.FAILED,
                fetched_at=utc_now(),
                error="Workday official detail withheld application",
            )
        description = strip_html(info.get("jobDescription"))
        application_url = info.get("externalUrl")
        req_id = str(info.get("jobReqId") or "").strip()
        if (
            req_id != job.job_id
            or not description
            or not valid_https_url(application_url)
            or not urlsplit(application_url).path.endswith(external_path)
        ):
            return DetailResult(
                job=None,
                status=DetailStatus.FAILED,
                fetched_at=utc_now(),
                error="Workday detail schema mismatch",
            )
        raw_locations: list[str] = []
        if isinstance(info.get("location"), str) and info["location"].strip():
            raw_locations.append(info["location"].strip())
        additional = info.get("additionalLocations")
        if isinstance(additional, list):
            raw_locations.extend(
                value.strip()
                for value in additional
                if isinstance(value, str) and value.strip()
            )
        raw_locations = list(dict.fromkeys(raw_locations))
        primary = raw_locations[0] if raw_locations else job.location
        location, city, region, inferred_country = parse_display_location(primary)
        country_node = info.get("jobRequisitionLocation")
        country_value = None
        if isinstance(country_node, dict):
            country = country_node.get("country")
            if isinstance(country, dict):
                country_value = country.get("alpha2Code")
        country_code = normalize_country_code(country_value) or inferred_country
        employment = normalize_employment_type(info.get("timeType"))
        provenance = dict(job.provenance)
        for field_name in (
            "description",
            "url",
            "requisition_id",
            "employment_type",
            "location",
        ):
            provenance[field_name] = FactSource.OFFICIAL_DETAIL
        if info.get("startDate"):
            provenance["posted_at"] = FactSource.OFFICIAL_DETAIL
        if country_code:
            provenance["country_code"] = FactSource.OFFICIAL_DETAIL
        return DetailResult(
            job=replace(
                job,
                title=(info.get("title") or job.title),
                requisition_id=req_id,
                location=", ".join(raw_locations) if raw_locations else location,
                city=city,
                region=region,
                country_code=country_code,
                url=application_url,
                description=description,
                employment_type=employment,
                posted_at=info.get("startDate") or job.posted_at,
                provenance=provenance,
            ),
            status=DetailStatus.HEALTHY,
            fetched_at=utc_now(),
        )
