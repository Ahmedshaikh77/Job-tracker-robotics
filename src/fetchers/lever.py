"""Complete Lever postings and official detail adapter."""
from __future__ import annotations

import hashlib
import json
from dataclasses import replace

from ..models import (
    DetailResult,
    DetailStatus,
    FactSource,
    FetchContext,
    FetchHealth,
    FetchResult,
    Job,
    SalaryRange,
)
from .base import (
    Fetcher,
    empty_valid_fetch,
    failed_fetch,
    http_status,
    normalize_country_code,
    normalize_employment_type,
    normalize_pay_period,
    normalize_workplace_type,
    parse_display_location,
    register,
    safe_decimal,
    source_key,
    strip_html,
    utc_now,
    valid_https_url,
)


def _body(row: dict) -> str:
    parts: list[str] = []
    primary = row.get("descriptionPlain") or strip_html(row.get("description"))
    if isinstance(primary, str) and primary.strip():
        parts.append(primary.strip())
    lists = row.get("lists")
    if isinstance(lists, list):
        for section in lists:
            if not isinstance(section, dict):
                continue
            content = strip_html(section.get("content"))
            if content:
                parts.append(content)
    additional = strip_html(row.get("additional"))
    if additional:
        parts.append(additional)
    return "\n\n".join(dict.fromkeys(parts))


def _salary(row: dict) -> SalaryRange | None:
    value = row.get("salaryRange")
    if not isinstance(value, dict):
        return None
    minimum = safe_decimal(value.get("min"))
    maximum = safe_decimal(value.get("max"))
    if minimum is None and maximum is None:
        return None
    currency = value.get("currency")
    return SalaryRange(
        minimum=minimum,
        maximum=maximum,
        currency=currency.strip().upper() if isinstance(currency, str) else "",
        period=normalize_pay_period(value.get("interval")),
        source=FactSource.STRUCTURED_FEED,
    )


def _parse_job(company: dict, row: dict) -> Job:
    job_id = str(row.get("id", "")).strip()
    title = row.get("text")
    apply_url = row.get("applyUrl")
    hosted_url = row.get("hostedUrl")
    application_url = apply_url if valid_https_url(apply_url) else hosted_url
    categories = row.get("categories") if isinstance(row.get("categories"), dict) else {}
    all_locations = categories.get("allLocations")
    location_value = categories.get("location")
    if not location_value and isinstance(all_locations, list):
        location_value = ", ".join(
            value for value in all_locations if isinstance(value, str) and value
        )
    description = _body(row)
    if (
        not job_id
        or not isinstance(title, str)
        or not title.strip()
        or not valid_https_url(application_url)
        or not description
    ):
        raise ValueError("Lever posting schema mismatch")
    location, city, region, inferred_country = parse_display_location(location_value)
    country = normalize_country_code(row.get("country")) or inferred_country
    employment = normalize_employment_type(categories.get("commitment"))
    workplace = normalize_workplace_type(row.get("workplaceType"))
    salary = _salary(row)
    provenance = {
        "title": FactSource.STRUCTURED_FEED,
        "url": FactSource.STRUCTURED_FEED,
        "description": FactSource.STRUCTURED_FEED,
    }
    for field_name, present in (
        ("location", bool(location)),
        ("country_code", bool(country)),
        ("employment_type", employment.value != "unknown"),
        ("workplace_type", workplace.value != "unknown"),
        ("salary", salary is not None),
    ):
        if present:
            provenance[field_name] = FactSource.STRUCTURED_FEED
    return Job(
        source_type="lever",
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
        employment_type=employment,
        workplace_type=workplace,
        salary=salary,
        metadata={
            "created_at": row.get("createdAt"),
            "raw_employment_type": categories.get("commitment"),
            "raw_workplace_type": row.get("workplaceType"),
            "all_locations": all_locations if isinstance(all_locations, list) else [],
            "team": categories.get("team"),
        },
        provenance=provenance,
    )


@register
class LeverFetcher(Fetcher):
    name = "lever"
    PAGE_SIZE = 100

    def fetch(self, company: dict, context: FetchContext) -> FetchResult:
        rows: list[dict] = []
        jobs: list[Job] = []
        seen: set[str] = set()
        page_signatures: set[str] = set()
        offset = 0
        pages = 0
        while True:
            url = (
                f"https://api.lever.co/v0/postings/{company['slug']}?mode=json"
                f"&skip={offset}&limit={self.PAGE_SIZE}"
            )
            try:
                response = self.http.get_json(url)
            except Exception:
                return failed_fetch(
                    context,
                    "Lever official board unavailable",
                    pages_fetched=pages,
                    partial=bool(pages),
                    jobs=tuple(jobs),
                )
            pages += 1
            page = response.data
            if not isinstance(page, list):
                return failed_fetch(
                    context,
                    "Lever board schema mismatch",
                    pages_fetched=pages,
                    partial=bool(rows),
                    jobs=tuple(jobs),
                )
            if not page and pages == 1:
                return empty_valid_fetch(pages_fetched=1)
            signature = hashlib.sha256(
                json.dumps(page, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest()
            if page and signature in page_signatures:
                return failed_fetch(
                    context,
                    "Lever repeated page",
                    pages_fetched=pages,
                    partial=True,
                    jobs=tuple(jobs),
                )
            page_signatures.add(signature)
            try:
                for row in page:
                    if not isinstance(row, dict):
                        raise ValueError
                    job = _parse_job(company, row)
                    if job.job_id in seen:
                        raise ValueError
                    seen.add(job.job_id)
                    rows.append(row)
                    jobs.append(job)
            except (TypeError, ValueError):
                return failed_fetch(
                    context,
                    "Lever posting schema mismatch",
                    pages_fetched=pages,
                    partial=True,
                    jobs=tuple(jobs),
                )
            if len(page) < self.PAGE_SIZE:
                break
            offset += self.PAGE_SIZE
        fingerprint = hashlib.sha256(
            json.dumps(rows, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        if context.previous_fingerprint == fingerprint:
            return FetchResult(
                jobs=(),
                active_ids=context.previous_active_ids,
                complete=True,
                source_total=len(context.previous_active_ids),
                pages_fetched=pages,
                fetched_at=utc_now(),
                health=FetchHealth.HEALTHY,
                fingerprint=fingerprint,
                unchanged=True,
            )
        return FetchResult(
            jobs=tuple(jobs),
            active_ids=frozenset(seen),
            complete=True,
            source_total=len(jobs),
            pages_fetched=pages,
            fetched_at=utc_now(),
            health=FetchHealth.HEALTHY,
            total_is_authoritative=False,
            fingerprint=fingerprint,
        )

    def fetch_detail(self, company: dict, job: Job) -> DetailResult:
        url = (
            f"https://api.lever.co/v0/postings/{company['slug']}/{job.job_id}"
            "?mode=json"
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
                    "Lever official detail closed"
                    if status in {404, 410}
                    else "Lever official detail unavailable"
                ),
            )
        if not isinstance(payload, dict) or str(payload.get("id", "")) != job.job_id:
            return DetailResult(
                job=None,
                status=DetailStatus.FAILED,
                fetched_at=utc_now(),
                error="Lever detail schema mismatch",
            )
        try:
            detail_job = _parse_job(company, payload)
        except (TypeError, ValueError):
            return DetailResult(
                job=None,
                status=DetailStatus.FAILED,
                fetched_at=utc_now(),
                error="Lever detail schema mismatch",
            )
        provenance = dict(detail_job.provenance)
        provenance["description"] = FactSource.OFFICIAL_DETAIL
        provenance["url"] = FactSource.OFFICIAL_DETAIL
        return DetailResult(
            job=replace(detail_job, provenance=provenance),
            status=DetailStatus.HEALTHY,
            fetched_at=utc_now(),
        )
