"""Complete Ashby job-board adapter."""
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
    SalaryRange,
)
from .base import (
    Fetcher,
    empty_valid_fetch,
    failed_fetch,
    normalize_employment_type,
    normalize_pay_period,
    normalize_workplace_type,
    not_modified_fetch,
    parse_display_location,
    register,
    safe_decimal,
    source_key,
    strip_html,
    utc_now,
    valid_https_url,
)


def _salary(row: dict) -> SalaryRange | None:
    compensation = row.get("compensation")
    if not isinstance(compensation, dict):
        return None
    components = compensation.get("summaryComponents")
    if not isinstance(components, list):
        return None
    for component in components:
        if not isinstance(component, dict) or component.get("compensationType") != "Salary":
            continue
        minimum = safe_decimal(component.get("minValue"))
        maximum = safe_decimal(component.get("maxValue"))
        currency = component.get("currencyCode")
        if minimum is None and maximum is None:
            continue
        return SalaryRange(
            minimum=minimum,
            maximum=maximum,
            currency=currency.strip().upper() if isinstance(currency, str) else "",
            period=normalize_pay_period(component.get("interval")),
            source=FactSource.STRUCTURED_FEED,
        )
    return None


@register
class AshbyFetcher(Fetcher):
    name = "ashby"

    def fetch(self, company: dict, context: FetchContext) -> FetchResult:
        url = (
            f"https://api.ashbyhq.com/posting-api/job-board/{company['slug']}"
            "?includeCompensation=true"
        )
        etag = None if context.force_full else context.previous_etag
        try:
            response = self.http.get_json(url, etag=etag)
        except Exception:
            return failed_fetch(context, "Ashby official board unavailable")
        if response.not_modified:
            return not_modified_fetch(context, response.etag or etag)
        payload = response.data
        if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), list):
            return failed_fetch(context, "Ashby board schema mismatch")
        rows = payload["jobs"]
        if not rows:
            return empty_valid_fetch(etag=response.etag)
        jobs: list[Job] = []
        seen: set[str] = set()
        try:
            for row in rows:
                if not isinstance(row, dict):
                    raise ValueError
                job_id = str(row.get("id", "")).strip()
                title = row.get("title")
                application_url = row.get("jobUrl") or row.get("applyUrl")
                description = row.get("descriptionPlain") or strip_html(
                    row.get("descriptionHtml")
                )
                if (
                    not job_id
                    or job_id in seen
                    or not isinstance(title, str)
                    or not title.strip()
                    or not valid_https_url(application_url)
                    or not isinstance(description, str)
                    or not description.strip()
                ):
                    raise ValueError
                seen.add(job_id)
                raw_location = row.get("locationName") or row.get("location") or ""
                location, city, region, country = parse_display_location(raw_location)
                employment = normalize_employment_type(row.get("employmentType"))
                workplace = normalize_workplace_type(row.get("workplaceType"))
                salary = _salary(row)
                metadata = {
                    "detail_complete": True,
                    "raw_employment_type": row.get("employmentType"),
                    "raw_workplace_type": row.get("workplaceType"),
                }
                provenance = {
                    "title": FactSource.STRUCTURED_FEED,
                    "url": FactSource.STRUCTURED_FEED,
                    "description": FactSource.STRUCTURED_FEED,
                }
                if location:
                    provenance["location"] = FactSource.STRUCTURED_FEED
                if row.get("publishedAt"):
                    provenance["posted_at"] = FactSource.STRUCTURED_FEED
                if employment.value != "unknown":
                    provenance["employment_type"] = FactSource.STRUCTURED_FEED
                if workplace.value != "unknown":
                    provenance["workplace_type"] = FactSource.STRUCTURED_FEED
                if salary:
                    provenance["salary"] = FactSource.STRUCTURED_FEED
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
                        description=description.strip(),
                        employment_type=employment,
                        workplace_type=workplace,
                        posted_at=row.get("publishedAt") or None,
                        salary=salary,
                        metadata=metadata,
                        provenance=provenance,
                    )
                )
        except (TypeError, ValueError):
            return failed_fetch(
                context,
                "Ashby posting schema mismatch",
                pages_fetched=1,
                partial=True,
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
        if (
            job.source_key != source_key(company)
            or job.metadata.get("detail_complete") is not True
            or not job.description
            or not valid_https_url(job.url)
            or job.provenance.get("description") is not FactSource.STRUCTURED_FEED
        ):
            return DetailResult(
                job=None,
                status=DetailStatus.FAILED,
                fetched_at=utc_now(),
                error="Ashby full board record was not verified",
            )
        provenance = dict(job.provenance)
        provenance["description"] = FactSource.OFFICIAL_DETAIL
        return DetailResult(
            job=replace(job, provenance=provenance),
            status=DetailStatus.HEALTHY,
            fetched_at=utc_now(),
        )
