"""Fetcher contract, registry, and shared normalization helpers."""
from __future__ import annotations

import html
import re
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import ClassVar
from urllib.parse import urlsplit

from ..models import (
    DetailResult,
    DetailStatus,
    EmploymentType,
    FetchContext,
    FetchHealth,
    FetchResult,
    Job,
    PayPeriod,
    WorkplaceType,
)
from .http import HttpClient


class Fetcher(ABC):
    name: ClassVar[str] = ""

    def __init__(self, http: HttpClient | None = None) -> None:
        self.http = http or HttpClient()

    @abstractmethod
    def fetch(self, company: dict, context: FetchContext) -> FetchResult:
        raise NotImplementedError

    def fetch_detail(self, company: dict, job: Job) -> DetailResult:
        return DetailResult(
            job=None,
            status=DetailStatus.FAILED,
            fetched_at=utc_now(),
            error="detail verification is not implemented for this adapter",
        )


_REGISTRY: dict[str, type[Fetcher]] = {}


def register(cls: type[Fetcher]) -> type[Fetcher]:
    if not cls.name:
        raise ValueError(f"{cls} missing 'name' class attribute")
    _REGISTRY[cls.name] = cls
    return cls


def get_fetcher(name: str, http: HttpClient | None = None) -> Fetcher:
    if name not in _REGISTRY:
        raise KeyError(f"No fetcher named '{name}'. Available: {sorted(_REGISTRY)}")
    return _REGISTRY[name](http=http)


def available() -> list[str]:
    return sorted(_REGISTRY)


def source_key(company: dict) -> str:
    fetcher = company.get("fetcher")
    if not isinstance(fetcher, str) or not fetcher:
        raise ValueError("source fetcher is required")
    if fetcher == "workday":
        fields = ("host", "tenant", "site")
    elif fetcher in {"greenhouse", "ashby", "lever", "smartrecruiters", "gem"}:
        fields = ("slug",)
    elif fetcher in {"rippling", "tesla"}:
        fields = ("board",)
    elif fetcher == "amazon":
        fields = ("source_id",)
    else:
        raise ValueError(f"fetcher has no canonical source identity: {fetcher}")
    values: list[str] = []
    for field_name in fields:
        value = company.get(field_name)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"source identity field is required: {field_name}")
        if ":" in value:
            raise ValueError(f"source identity field cannot contain colon: {field_name}")
        values.append(value.strip())
    return ":".join((fetcher, *values))


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def http_status(error: Exception) -> int | None:
    response = getattr(error, "response", None)
    value = getattr(response, "status_code", None)
    return int(value) if isinstance(value, int) else None


def failed_fetch(
    context: FetchContext,
    error: str,
    *,
    pages_fetched: int = 0,
    partial: bool = False,
    jobs: tuple[Job, ...] = (),
) -> FetchResult:
    return FetchResult(
        jobs=jobs,
        active_ids=context.previous_active_ids,
        complete=False,
        source_total=None,
        pages_fetched=pages_fetched,
        fetched_at=utc_now(),
        health=FetchHealth.PARTIAL if partial else FetchHealth.FAILED,
        error=error,
    )


def not_modified_fetch(context: FetchContext, etag: str | None) -> FetchResult:
    return FetchResult(
        jobs=(),
        active_ids=context.previous_active_ids,
        complete=True,
        source_total=len(context.previous_active_ids),
        pages_fetched=1,
        fetched_at=utc_now(),
        health=FetchHealth.HEALTHY,
        etag=etag,
        fingerprint=context.previous_fingerprint,
        unchanged=True,
    )


def empty_valid_fetch(*, pages_fetched: int = 1, etag: str | None = None) -> FetchResult:
    return FetchResult(
        jobs=(),
        active_ids=frozenset(),
        complete=True,
        source_total=0,
        pages_fetched=pages_fetched,
        fetched_at=utc_now(),
        health=FetchHealth.EMPTY_VALID,
        total_is_authoritative=True,
        etag=etag,
    )


def safe_decimal(value: object) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return result if result.is_finite() else None


def strip_html(value: object) -> str:
    if not isinstance(value, str) or not value:
        return ""
    without_tags = re.sub(r"<[^>]+>", " ", value)
    return re.sub(r"\s+", " ", html.unescape(without_tags)).strip()


def valid_https_url(value: object) -> bool:
    if not isinstance(value, str) or not value:
        return False
    parsed = urlsplit(value)
    return parsed.scheme == "https" and bool(parsed.hostname) and not (
        parsed.username or parsed.password
    )


def normalize_country_code(value: object) -> str:
    if not isinstance(value, str):
        return ""
    normalized = value.strip().upper()
    aliases = {
        "USA": "US",
        "UNITED STATES": "US",
        "UNITED STATES OF AMERICA": "US",
        "LUX": "LU",
    }
    if normalized in aliases:
        return aliases[normalized]
    return normalized if len(normalized) == 2 and normalized.isalpha() else ""


_US_REGIONS = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI",
    "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI",
    "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC",
    "ND", "OH", "OK", "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT",
    "VT", "VA", "WA", "WV", "WI", "WY", "DC",
}


def parse_display_location(value: object) -> tuple[str, str, str, str]:
    if not isinstance(value, str):
        return "", "", "", ""
    display = value.strip()
    if not display:
        return "", "", "", ""
    parts = [part.strip() for part in display.split(",") if part.strip()]
    city = parts[0] if parts else ""
    region = parts[1] if len(parts) >= 2 else ""
    country = normalize_country_code(parts[-1]) if len(parts) >= 3 else ""
    if not country and region.upper() in _US_REGIONS:
        country = "US"
    return display, city, region, country


def normalize_employment_type(value: object) -> EmploymentType:
    raw = re.sub(r"[^a-z]", "", str(value or "").lower())
    if raw in {"fulltime", "salariedft", "regularfulltime"}:
        return EmploymentType.FULL_TIME
    if raw == "parttime":
        return EmploymentType.PART_TIME
    if raw in {"contract", "contractor"}:
        return EmploymentType.CONTRACT
    if raw in {"temporary", "temp"}:
        return EmploymentType.TEMPORARY
    if raw in {"intern", "internship"}:
        return EmploymentType.INTERNSHIP
    return EmploymentType.UNKNOWN


def normalize_workplace_type(value: object) -> WorkplaceType:
    raw = re.sub(r"[^a-z]", "", str(value or "").lower())
    if raw in {"onsite", "onsiteonly"}:
        return WorkplaceType.ONSITE
    if raw == "hybrid":
        return WorkplaceType.HYBRID
    if raw in {"remote", "remotefirst"}:
        return WorkplaceType.REMOTE
    return WorkplaceType.UNKNOWN


def normalize_pay_period(value: object) -> PayPeriod:
    raw = str(value or "").strip().lower()
    if raw in {"1 year", "year", "yearly", "annual", "per-year-salary"}:
        return PayPeriod.YEAR
    if raw in {"hour", "hourly", "1 hour", "per-hour-salary"}:
        return PayPeriod.HOUR
    return PayPeriod.UNKNOWN
