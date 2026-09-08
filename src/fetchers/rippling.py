"""Schema-guarded provisional Rippling adapter."""
from __future__ import annotations

from dataclasses import replace

from ..models import (
    DetailResult,
    DetailStatus,
    EmploymentType,
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
    parse_display_location,
    register,
    source_key,
    strip_html,
    utc_now,
    valid_https_url,
)


@register
class RipplingFetcher(Fetcher):
    name = "rippling"

    def fetch(self, company: dict, context: FetchContext) -> FetchResult:
        endpoint = (
            "https://api.rippling.com/platform/api/ats/v1/board/"
            f"{company['board']}/jobs"
        )
        try:
            payload = self.http.get_json(endpoint).data
        except Exception:
            return failed_fetch(context, "Rippling official board unavailable")
        if not isinstance(payload, list):
            return failed_fetch(context, "Rippling board schema mismatch")
        if not payload:
            return empty_valid_fetch()
        grouped: dict[str, dict] = {}
        try:
            for row in payload:
                if not isinstance(row, dict):
                    raise ValueError
                job_id = str(row.get("uuid", "")).strip()
                title = row.get("name")
                url = row.get("url")
                department = row.get("department")
                work_location = row.get("workLocation")
                if (
                    not job_id
                    or not isinstance(title, str)
                    or not title.strip()
                    or not valid_https_url(url)
                    or not isinstance(department, dict)
                    or not isinstance(work_location, dict)
                    or not isinstance(work_location.get("id"), str)
                    or not isinstance(work_location.get("label"), str)
                ):
                    raise ValueError
                identity = (title.strip(), url, department.get("id"), department.get("label"))
                existing = grouped.get(job_id)
                if existing is None:
                    grouped[job_id] = {
                        "identity": identity,
                        "row": row,
                        "locations": {
                            (work_location["id"], work_location["label"])
                        },
                    }
                else:
                    if existing["identity"] != identity:
                        raise ValueError
                    existing["locations"].add(
                        (work_location["id"], work_location["label"])
                    )
        except (TypeError, ValueError):
            return failed_fetch(
                context,
                "Rippling posting schema mismatch",
                pages_fetched=1,
                partial=True,
            )
        jobs: list[Job] = []
        for job_id in sorted(grouped):
            entry = grouped[job_id]
            row = entry["row"]
            locations = sorted(entry["locations"])
            labels = [label for _, label in locations]
            display = "; ".join(labels)
            _, city, region, country = parse_display_location(labels[0])
            jobs.append(
                Job(
                    source_type=self.name,
                    source_key=source_key(company),
                    company=company["name"],
                    job_id=job_id,
                    title=row["name"].strip(),
                    location=display,
                    city=city,
                    region=region,
                    country_code=country,
                    url=row["url"],
                    metadata={
                        "department": row.get("department"),
                        "locations": [
                            {"id": location_id, "label": label}
                            for location_id, label in locations
                        ],
                    },
                    provenance={
                        "title": FactSource.STRUCTURED_FEED,
                        "url": FactSource.STRUCTURED_FEED,
                        "location": FactSource.STRUCTURED_FEED,
                    },
                )
            )
        return FetchResult(
            jobs=tuple(jobs),
            active_ids=frozenset(grouped),
            complete=True,
            source_total=len(grouped),
            pages_fetched=1,
            fetched_at=utc_now(),
            health=FetchHealth.HEALTHY,
            total_is_authoritative=False,
        )

    def fetch_detail(self, company: dict, job: Job) -> DetailResult:
        endpoint = (
            "https://api.rippling.com/platform/api/ats/v1/board/"
            f"{company['board']}/jobs/{job.job_id}"
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
                    "Rippling official detail closed"
                    if status in {404, 410}
                    else "Rippling official detail unavailable"
                ),
            )
        board = payload.get("board") if isinstance(payload, dict) else None
        if (
            not isinstance(payload, dict)
            or str(payload.get("uuid", "")) != job.job_id
            or not isinstance(board, dict)
            or board.get("slug") != company["board"]
        ):
            return DetailResult(
                job=None,
                status=DetailStatus.FAILED,
                fetched_at=utc_now(),
                error="Rippling detail schema mismatch",
            )
        if payload.get("unlistedFromSearch") is True:
            return DetailResult(
                job=None,
                status=DetailStatus.FAILED,
                fetched_at=utc_now(),
                error="Rippling official detail not publicly listed",
            )
        description_node = payload.get("description")
        role = description_node.get("role") if isinstance(description_node, dict) else None
        description = strip_html(role)
        employment_node = payload.get("employmentType")
        if (
            not description
            or not isinstance(employment_node, dict)
            or not isinstance(payload.get("workLocations"), list)
        ):
            return DetailResult(
                job=None,
                status=DetailStatus.FAILED,
                fetched_at=utc_now(),
                error="Rippling detail schema mismatch",
            )
        employment = (
            EmploymentType.FULL_TIME
            if employment_node.get("label") == "SALARIED_FT"
            else EmploymentType.UNKNOWN
        )
        locations = sorted(
            (
                str(value.get("id", "")),
                str(value.get("label", "")),
            )
            for value in payload["workLocations"]
            if isinstance(value, dict) and value.get("id") and value.get("label")
        )
        labels = [label for _, label in locations]
        first = labels[0] if labels else job.location
        _, city, region, country = parse_display_location(first)
        provenance = dict(job.provenance)
        provenance["description"] = FactSource.OFFICIAL_DETAIL
        provenance["employment_type"] = FactSource.OFFICIAL_DETAIL
        if payload.get("createdOn"):
            provenance["posted_at"] = FactSource.OFFICIAL_DETAIL
        return DetailResult(
            job=replace(
                job,
                description=description,
                location="; ".join(labels) if labels else job.location,
                city=city or job.city,
                region=region or job.region,
                country_code=country or job.country_code,
                employment_type=employment,
                posted_at=payload.get("createdOn") or job.posted_at,
                metadata={
                    **dict(job.metadata),
                    "locations": [
                        {"id": location_id, "label": label}
                        for location_id, label in locations
                    ],
                    "raw_employment_type": {
                        "id": employment_node.get("id"),
                        "label": employment_node.get("label"),
                    },
                },
                provenance=provenance,
            ),
            status=DetailStatus.HEALTHY,
            fetched_at=utc_now(),
        )
