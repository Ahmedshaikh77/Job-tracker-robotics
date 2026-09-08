"""Complete Amazon Jobs search and official HTML detail adapter."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import replace
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Callable
from urllib.parse import urljoin, urlsplit

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
    register,
    safe_decimal,
    source_key,
    strip_html,
    utc_now,
    valid_https_url,
)
from .http import HttpClient


class _AmazonPageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._script = False
        self._script_chunks: list[str] = []
        self.json_ld: list[str] = []
        self._anchor: dict[str, str | None] | None = None
        self.apply_links: list[tuple[str, bool]] = []
        self.semantic_apply_links: list[tuple[str, bool]] = []
        self.saw_job_detail = False
        self._title = False
        self._title_chunks: list[str] = []
        self._meta = False
        self._meta_chunks: list[str] = []
        self._section_depth = 0
        self._section_heading = False
        self._section_body_depth = 0
        self._section_heading_chunks: list[str] = []
        self._section_body_chunks: list[str] = []
        self.sections: list[tuple[str, str]] = []
        self._location_depth = 0
        self._location_item = False
        self._location_chunks: list[str] = []
        self.locations: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        values = {str(key).lower(): value for key, value in attrs}
        classes = set(str(values.get("class", "")).split())
        lowered_tag = tag.lower()
        if lowered_tag == "script" and values.get("type") == "application/ld+json":
            self._script = True
            self._script_chunks = []
        if lowered_tag == "div":
            if values.get("id") == "job-detail":
                self.saw_job_detail = True
            if self._section_depth:
                self._section_depth += 1
            elif "section" in classes:
                self._section_depth = 1
                self._section_heading_chunks = []
                self._section_body_chunks = []
            if self._location_depth:
                self._location_depth += 1
            elif {"association", "location-icon"}.issubset(classes):
                self._location_depth = 1
        if lowered_tag == "h1" and "title" in classes:
            self._title = True
        if lowered_tag == "p" and "meta" in classes:
            self._meta = True
        if lowered_tag == "h2" and self._section_depth:
            self._section_heading = True
        if lowered_tag == "p" and self._section_depth:
            self._section_body_depth += 1
        if lowered_tag == "br" and self._section_body_depth:
            self._section_body_chunks.append("\n")
        if lowered_tag == "li" and self._location_depth:
            self._location_item = True
            self._location_chunks = []
        if lowered_tag == "a":
            self._anchor = values

    def handle_data(self, data: str) -> None:
        if self._script:
            self._script_chunks.append(data)
        if self._title:
            self._title_chunks.append(data)
        if self._meta:
            self._meta_chunks.append(data)
        if self._section_heading:
            self._section_heading_chunks.append(data)
        elif self._section_body_depth:
            self._section_body_chunks.append(data)
        if self._location_item:
            self._location_chunks.append(data)
        if self._anchor is not None and "apply now" in data.lower():
            href = self._anchor.get("href")
            if href:
                disabled = (
                    str(self._anchor.get("aria-disabled", "")).lower() == "true"
                    or "disabled" in self._anchor
                )
                self.apply_links.append((str(href), disabled))
                if self._anchor.get("id") == "apply-button":
                    self.semantic_apply_links.append((str(href), disabled))

    def handle_endtag(self, tag: str) -> None:
        lowered_tag = tag.lower()
        if lowered_tag == "script" and self._script:
            self.json_ld.append("".join(self._script_chunks))
            self._script = False
            self._script_chunks = []
        if lowered_tag == "h1":
            self._title = False
        if lowered_tag == "h2":
            self._section_heading = False
        if lowered_tag == "p":
            self._meta = False
            if self._section_body_depth:
                self._section_body_depth -= 1
        if lowered_tag == "li" and self._location_item:
            location = " ".join("".join(self._location_chunks).split())
            if location:
                self.locations.append(location)
            self._location_item = False
            self._location_chunks = []
        if lowered_tag == "div":
            if self._section_depth:
                self._section_depth -= 1
                if self._section_depth == 0:
                    heading = " ".join(
                        "".join(self._section_heading_chunks).split()
                    )
                    body = "\n".join(
                        part.strip()
                        for part in "".join(self._section_body_chunks).splitlines()
                        if part.strip()
                    )
                    if heading or body:
                        self.sections.append((heading, body))
            if self._location_depth:
                self._location_depth -= 1
        if lowered_tag == "a":
            self._anchor = None

    @property
    def title(self) -> str:
        return " ".join("".join(self._title_chunks).split())

    @property
    def meta(self) -> str:
        return " ".join("".join(self._meta_chunks).split())


def _parse_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    normalized = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _parse_row(company: dict, row: dict) -> Job:
    job_id = str(row.get("id_icims", "")).strip()
    title = row.get("title")
    job_path = row.get("job_path")
    structured = row.get("normalized_location")
    if (
        not job_id
        or not isinstance(title, str)
        or not title.strip()
        or not isinstance(job_path, str)
        or not job_path.startswith("/en/jobs/")
    ):
        raise ValueError("Amazon posting schema mismatch")
    if isinstance(structured, dict):
        city = structured.get("city")
        region = structured.get("region")
        country = normalize_country_code(structured.get("country_code"))
    elif isinstance(structured, str) and structured.strip():
        # Captured from the current official search feed on 2026-09-07. The
        # display value moved to normalized_location while the exact location
        # components are exposed as top-level city/state/country_code fields.
        city = row.get("city")
        region = row.get("state")
        country = normalize_country_code(row.get("country_code"))
    else:
        raise ValueError("Amazon structured location is ambiguous")
    if (
        not isinstance(city, str)
        or not city.strip()
        or not isinstance(region, str)
        or not region.strip()
        or not country
    ):
        raise ValueError("Amazon structured location is ambiguous")
    display = structured if isinstance(structured, str) else row.get("location")
    if not isinstance(display, str) or not display.strip():
        display = ", ".join((city.strip(), region.strip(), country))
    description_parts = []
    for key in ("description", "basic_qualifications", "preferred_qualifications"):
        value = strip_html(row.get(key))
        if value:
            description_parts.append(value)
    if not description_parts:
        raise ValueError("Amazon posting has no description")
    application_url = urljoin("https://www.amazon.jobs", job_path)
    if not valid_https_url(application_url):
        raise ValueError("Amazon posting URL is invalid")
    provenance = {
        "title": FactSource.STRUCTURED_FEED,
        "url": FactSource.STRUCTURED_FEED,
        "description": FactSource.STRUCTURED_FEED,
        "location": FactSource.STRUCTURED_FEED,
        "country_code": FactSource.STRUCTURED_FEED,
    }
    if row.get("posted_date"):
        provenance["posted_at"] = FactSource.STRUCTURED_FEED
    if row.get("updated_time"):
        provenance["updated_at"] = FactSource.STRUCTURED_FEED
    return Job(
        source_type="amazon",
        source_key=source_key(company),
        company=company["name"],
        job_id=job_id,
        requisition_id=job_id,
        title=title.strip(),
        location=display.strip(),
        city=city.strip(),
        region=region.strip(),
        country_code=country,
        url=application_url,
        description="\n\n".join(description_parts),
        posted_at=row.get("posted_date") or None,
        updated_at=row.get("updated_time") or None,
        metadata={"job_path": job_path},
        provenance=provenance,
    )


def _parse_semantic_detail(parser: _AmazonPageParser, job: Job) -> Job:
    identifier_match = re.fullmatch(r"Job ID:\s*([^|\s]+)\s*\|\s*.+", parser.meta)
    enabled_links = [
        href for href, disabled in parser.semantic_apply_links if not disabled
    ]
    required_headings = {
        "Description",
        "Basic Qualifications",
        "Preferred Qualifications",
    }
    sections = {heading: body for heading, body in parser.sections}
    if (
        not parser.saw_job_detail
        or identifier_match is None
        or identifier_match.group(1) != job.job_id
        or not parser.title
        or len(enabled_links) != 1
        or len(parser.sections) != len(required_headings)
        or set(sections) != required_headings
        or any(not sections[heading] for heading in required_headings)
        or not parser.locations
    ):
        raise ValueError("Amazon semantic detail schema mismatch")
    locations: list[tuple[str, str, str]] = []
    normalized_locations: list[str] = []
    for raw_location in parser.locations:
        location_parts = [
            part.strip() for part in raw_location.split(",") if part.strip()
        ]
        if len(location_parts) < 3:
            raise ValueError("Amazon semantic detail location is ambiguous")
        country = normalize_country_code(location_parts[0])
        region = location_parts[1]
        city = ", ".join(location_parts[2:])
        if country != "US" or not region or not city:
            raise ValueError("Amazon semantic detail location is ambiguous")
        normalized = ", ".join((city, region, country))
        if normalized in normalized_locations:
            raise ValueError("Amazon semantic detail location is ambiguous")
        locations.append((city, region, country))
        normalized_locations.append(normalized)
    city, region, country = locations[0]
    application_url = urljoin("https://www.amazon.jobs", enabled_links[0])
    parsed_url = urlsplit(application_url)
    expected_path = f"/applicant/jobs/{job.job_id}/apply"
    if (
        not valid_https_url(application_url)
        or parsed_url.hostname != "www.amazon.jobs"
        or parsed_url.path != expected_path
    ):
        raise ValueError("Amazon semantic application URL is invalid")
    description = "\n\n".join(
        f"{heading}\n{sections[heading]}"
        for heading in (
            "Description",
            "Basic Qualifications",
            "Preferred Qualifications",
        )
    )
    provenance = dict(job.provenance)
    for field_name in ("title", "description", "location", "country_code", "url"):
        provenance[field_name] = FactSource.OFFICIAL_DETAIL
    metadata = dict(job.metadata)
    metadata["official_locations"] = normalized_locations
    return replace(
        job,
        title=parser.title,
        location="; ".join(normalized_locations),
        city=city,
        region=region,
        country_code=country,
        url=application_url,
        description=description,
        metadata=metadata,
        provenance=provenance,
    )


@register
class AmazonFetcher(Fetcher):
    name = "amazon"
    PAGE_SIZE = 100

    def __init__(
        self,
        http: HttpClient | None = None,
        *,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        super().__init__(http)
        self._now = now

    def fetch(self, company: dict, context: FetchContext) -> FetchResult:
        endpoint = "https://www.amazon.jobs/en/search.json"
        offset = 0
        pages = 0
        expected_total: int | None = None
        jobs: list[Job] = []
        seen: set[str] = set()
        page_signatures: set[str] = set()
        while True:
            params = {
                "base_query": company.get("search_query", "robotics"),
                "country": "USA",
                "loc_query": "United States",
                "latitude": "38.89037",
                "longitude": "-77.03196",
                "type": "area",
                "result_limit": self.PAGE_SIZE,
                "offset": offset,
                "sort": "recent",
            }
            try:
                page = self.http.get_json(endpoint, params=params).data
            except Exception:
                return failed_fetch(
                    context,
                    "Amazon official search unavailable",
                    pages_fetched=pages,
                    partial=bool(pages),
                    jobs=tuple(jobs),
                )
            pages += 1
            if not isinstance(page, dict):
                return failed_fetch(
                    context,
                    "Amazon search schema mismatch",
                    pages_fetched=pages,
                    partial=bool(jobs),
                    jobs=tuple(jobs),
                )
            total = page.get("hits")
            rows = page.get("jobs")
            if (
                isinstance(total, bool)
                or not isinstance(total, int)
                or total < 0
                or not isinstance(rows, list)
            ):
                return failed_fetch(
                    context,
                    "Amazon search schema mismatch",
                    pages_fetched=pages,
                    partial=bool(jobs),
                    jobs=tuple(jobs),
                )
            if expected_total is None:
                expected_total = total
            elif total != expected_total:
                return failed_fetch(
                    context,
                    "Amazon total changed during pagination",
                    pages_fetched=pages,
                    partial=True,
                    jobs=tuple(jobs),
                )
            if total == 0 and rows == [] and pages == 1:
                return empty_valid_fetch()
            if not rows and offset < total:
                return failed_fetch(
                    context,
                    "Amazon pagination ended before total",
                    pages_fetched=pages,
                    partial=bool(jobs),
                    jobs=tuple(jobs),
                )
            signature = hashlib.sha256(
                json.dumps(rows, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest()
            if rows and signature in page_signatures:
                return failed_fetch(
                    context,
                    "Amazon repeated page",
                    pages_fetched=pages,
                    partial=True,
                    jobs=tuple(jobs),
                )
            page_signatures.add(signature)
            try:
                for row in rows:
                    if not isinstance(row, dict):
                        raise ValueError
                    job = _parse_row(company, row)
                    if job.job_id in seen:
                        raise ValueError
                    seen.add(job.job_id)
                    jobs.append(job)
            except (TypeError, ValueError):
                return failed_fetch(
                    context,
                    "Amazon posting schema mismatch",
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
                    "Amazon short page before total",
                    pages_fetched=pages,
                    partial=True,
                    jobs=tuple(jobs),
                )
        if expected_total is None or len(seen) != expected_total:
            return failed_fetch(
                context,
                "Amazon final count mismatch",
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
        try:
            html = self.http.get_text(job.url).text
        except Exception as exc:
            status = http_status(exc)
            return DetailResult(
                job=None,
                status=(DetailStatus.CLOSED if status in {404, 410} else DetailStatus.FAILED),
                fetched_at=utc_now(),
                error=(
                    "Amazon official detail closed"
                    if status in {404, 410}
                    else "Amazon official detail unavailable"
                ),
            )
        lowered = html.lower()
        if any(
            marker in lowered
            for marker in ("job is no longer available", "position has been filled")
        ):
            return DetailResult(
                job=None,
                status=DetailStatus.FAILED,
                fetched_at=utc_now(),
                error="Amazon official detail withheld application",
            )
        parser = _AmazonPageParser()
        try:
            parser.feed(html)
        except Exception:
            parser = _AmazonPageParser()
        postings: list[dict] = []
        for raw in parser.json_ld:
            try:
                value = json.loads(raw)
            except (TypeError, ValueError):
                continue
            candidates = value if isinstance(value, list) else [value]
            postings.extend(
                candidate
                for candidate in candidates
                if isinstance(candidate, dict) and candidate.get("@type") == "JobPosting"
            )
        enabled_links = [href for href, disabled in parser.apply_links if not disabled]
        if not postings:
            try:
                semantic_job = _parse_semantic_detail(parser, job)
            except ValueError:
                pass
            else:
                return DetailResult(
                    job=semantic_job,
                    status=DetailStatus.HEALTHY,
                    fetched_at=utc_now(),
                )
        if len(postings) != 1 or len(enabled_links) != 1:
            return DetailResult(
                job=None,
                status=DetailStatus.FAILED,
                fetched_at=utc_now(),
                error="Amazon detail schema mismatch",
            )
        posting = postings[0]
        identifier = posting.get("identifier")
        title = posting.get("title")
        description = strip_html(posting.get("description"))
        date_posted = posting.get("datePosted")
        if (
            not isinstance(identifier, dict)
            or str(identifier.get("value", "")) != job.job_id
            or not isinstance(title, str)
            or not title.strip()
            or not description
            or _parse_datetime(date_posted) is None
        ):
            return DetailResult(
                job=None,
                status=DetailStatus.FAILED,
                fetched_at=utc_now(),
                error="Amazon detail schema mismatch",
            )
        valid_through = posting.get("validThrough")
        if valid_through is not None:
            expiry = _parse_datetime(valid_through)
            if expiry is None or expiry < self._now().astimezone(timezone.utc):
                return DetailResult(
                    job=None,
                    status=DetailStatus.FAILED,
                    fetched_at=utc_now(),
                    error="Amazon official detail withheld application",
                )
        salary = None
        salary_node = posting.get("baseSalary")
        if isinstance(salary_node, dict):
            value = salary_node.get("value")
            if isinstance(value, dict):
                minimum = safe_decimal(value.get("minValue"))
                maximum = safe_decimal(value.get("maxValue"))
                currency = salary_node.get("currency")
                if (minimum is not None or maximum is not None) and isinstance(currency, str):
                    salary = SalaryRange(
                        minimum=minimum,
                        maximum=maximum,
                        currency=currency.strip().upper(),
                        period=normalize_pay_period(value.get("unitText")),
                        source=FactSource.OFFICIAL_DETAIL,
                    )
        location_node = posting.get("jobLocation")
        address = location_node.get("address") if isinstance(location_node, dict) else None
        city = address.get("addressLocality") if isinstance(address, dict) else ""
        region = address.get("addressRegion") if isinstance(address, dict) else ""
        country = normalize_country_code(
            address.get("addressCountry") if isinstance(address, dict) else ""
        )
        if not isinstance(city, str) or not isinstance(region, str) or not country:
            return DetailResult(
                job=None,
                status=DetailStatus.FAILED,
                fetched_at=utc_now(),
                error="Amazon detail schema mismatch",
            )
        application_url = urljoin("https://www.amazon.jobs", enabled_links[0])
        if not valid_https_url(application_url):
            return DetailResult(
                job=None,
                status=DetailStatus.FAILED,
                fetched_at=utc_now(),
                error="Amazon detail schema mismatch",
            )
        provenance = dict(job.provenance)
        for field_name in (
            "title",
            "description",
            "posted_at",
            "employment_type",
            "location",
            "country_code",
            "url",
        ):
            provenance[field_name] = FactSource.OFFICIAL_DETAIL
        if salary:
            provenance["salary"] = FactSource.OFFICIAL_DETAIL
        return DetailResult(
            job=replace(
                job,
                title=title.strip(),
                location=", ".join((city, region, country)),
                city=city,
                region=region,
                country_code=country,
                url=application_url,
                description=description,
                employment_type=normalize_employment_type(posting.get("employmentType")),
                posted_at=date_posted,
                salary=salary,
                provenance=provenance,
            ),
            status=DetailStatus.HEALTHY,
            fetched_at=utc_now(),
        )
