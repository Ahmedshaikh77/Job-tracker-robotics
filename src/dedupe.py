"""Canonical identity and conservative duplicate detection helpers."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .models import FactSource, Job


_TRACKING_KEYS = {"gh_src", "lever-source", "source"}
_STATE_NAMES = {
    "alabama": "al",
    "alaska": "ak",
    "arizona": "az",
    "arkansas": "ar",
    "california": "ca",
    "colorado": "co",
    "connecticut": "ct",
    "delaware": "de",
    "florida": "fl",
    "georgia": "ga",
    "hawaii": "hi",
    "idaho": "id",
    "illinois": "il",
    "indiana": "in",
    "iowa": "ia",
    "kansas": "ks",
    "kentucky": "ky",
    "louisiana": "la",
    "maine": "me",
    "maryland": "md",
    "massachusetts": "ma",
    "michigan": "mi",
    "minnesota": "mn",
    "mississippi": "ms",
    "missouri": "mo",
    "montana": "mt",
    "nebraska": "ne",
    "nevada": "nv",
    "new hampshire": "nh",
    "new jersey": "nj",
    "new mexico": "nm",
    "new york": "ny",
    "north carolina": "nc",
    "north dakota": "nd",
    "ohio": "oh",
    "oklahoma": "ok",
    "oregon": "or",
    "pennsylvania": "pa",
    "rhode island": "ri",
    "south carolina": "sc",
    "south dakota": "sd",
    "tennessee": "tn",
    "texas": "tx",
    "utah": "ut",
    "vermont": "vt",
    "virginia": "va",
    "washington": "wa",
    "west virginia": "wv",
    "wisconsin": "wi",
    "wyoming": "wy",
}


def normalize_text(value: str) -> str:
    """Normalize human text for identity comparisons, not display."""
    return " ".join(re.sub(r"[^a-z0-9]+", " ", str(value).casefold()).split())


def normalize_official_url(value: str) -> str:
    """Normalize a valid HTTP(S) application URL and remove tracking data."""
    if not value:
        return ""
    try:
        parsed = urlsplit(value.strip())
    except ValueError:
        return ""
    scheme = parsed.scheme.casefold()
    if scheme not in {"http", "https"} or not parsed.hostname:
        return ""
    host = parsed.hostname.casefold()
    try:
        port = parsed.port
    except ValueError:
        return ""
    if port is not None and not (
        (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
    ):
        host = f"{host}:{port}"
    if parsed.username or parsed.password:
        return ""
    path = parsed.path.rstrip("/") or "/"
    query = [
        (key, item)
        for key, item in parse_qsl(parsed.query, keep_blank_values=True)
        if not key.casefold().startswith("utm_")
        and key.casefold() not in _TRACKING_KEYS
    ]
    return urlunsplit((scheme, host, path, urlencode(sorted(query)), ""))


def _trusted_requisition(job: Job) -> bool:
    source = job.provenance.get("requisition_id", FactSource.UNAVAILABLE)
    return bool(job.requisition_id) and source in {
        FactSource.STRUCTURED_FEED,
        FactSource.OFFICIAL_DETAIL,
    }


def candidate_aliases(job: Job) -> tuple[str, ...]:
    aliases: set[str] = {f"source:{job.source_key}:{job.job_id}"}
    if _trusted_requisition(job):
        aliases.add(
            f"req:{normalize_text(job.company)}:{normalize_text(job.requisition_id)}"
        )
    normalized_url = normalize_official_url(job.url)
    if normalized_url:
        aliases.add(f"url:{normalized_url}")
    return tuple(sorted(aliases))


def durable_identity_aliases(job: Job) -> tuple[str, ...]:
    return tuple(
        alias
        for alias in candidate_aliases(job)
        if alias.startswith(("req:", "source:"))
    )


def candidate_aliases_from_legacy(
    company: str, posting_id: str, url: str
) -> list[str]:
    aliases = {f"legacy-local:{normalize_text(company)}:{normalize_text(posting_id)}"}
    normalized_url = normalize_official_url(url)
    if normalized_url:
        aliases.add(f"url:{normalized_url}")
    return sorted(aliases)


def durable_identity_aliases_from_legacy(
    company: str, posting_id: str, url: str
) -> list[str]:
    del url
    return [f"legacy-local:{normalize_text(company)}:{normalize_text(posting_id)}"]


def canonical_candidate_id(company: str, posting_id: str, url: str) -> str:
    normalized_url = normalize_official_url(url)
    if normalized_url:
        return f"url:{normalized_url}"
    return f"legacy-local:{normalize_text(company)}:{normalize_text(posting_id)}"


def _record_has_alias(record: Mapping[str, Any], alias: str) -> bool:
    return alias in record.get("aliases", ())


def resolve_candidate_id(
    job: Job, candidates: Mapping[str, Mapping[str, Any]]
) -> str:
    aliases = candidate_aliases(job)
    req_aliases = [alias for alias in aliases if alias.startswith("req:")]
    url_aliases = [alias for alias in aliases if alias.startswith("url:")]
    source_alias = next(alias for alias in aliases if alias.startswith("source:"))

    for alias in req_aliases:
        for candidate_id, record in sorted(candidates.items()):
            if _record_has_alias(record, alias):
                return candidate_id
    for alias in url_aliases:
        for candidate_id, record in sorted(candidates.items()):
            if record.get("closed_at") is None and _record_has_alias(record, alias):
                return candidate_id
    for candidate_id, record in sorted(candidates.items()):
        if _record_has_alias(record, source_alias):
            return candidate_id

    legacy_alias = (
        f"legacy-local:{normalize_text(job.company)}:{normalize_text(job.job_id)}"
    )
    for candidate_id, record in sorted(candidates.items()):
        if record.get("migration_baseline_pending") and _record_has_alias(
            record, legacy_alias
        ):
            return candidate_id
    # With a candidate index, a URL that belongs only to a closed record must
    # not be reused as the new record's key. The source-scoped alias is stable
    # and cannot collide with that closed repost.
    return source_alias


def canonical_job_key(
    job: Job, candidates: Mapping[str, Mapping[str, Any]] | None = None
) -> str:
    if candidates is not None:
        return resolve_candidate_id(job, candidates)
    aliases = candidate_aliases(job)
    for prefix in ("req:", "url:", "source:"):
        match = next((alias for alias in aliases if alias.startswith(prefix)), None)
        if match:
            return match
    raise AssertionError("a source alias is always present")


def _normalize_location(value: str) -> str:
    normalized = normalize_text(value)
    for name, abbreviation in _STATE_NAMES.items():
        normalized = re.sub(
            rf"\b{re.escape(name)}\b", abbreviation, normalized, flags=re.IGNORECASE
        )
    return normalize_text(normalized)


def probable_duplicate_key(job: Job) -> str:
    """Return a review hint that must never be used for an automatic merge."""
    return "|".join(
        (
            normalize_text(job.company),
            normalize_text(job.title),
            _normalize_location(job.location),
        )
    )


def find_candidate_snapshot(
    job: Job, candidates: Mapping[str, Mapping[str, Any]]
) -> Mapping[str, Any] | None:
    candidate_id = resolve_candidate_id(job, candidates)
    return candidates.get(candidate_id)
