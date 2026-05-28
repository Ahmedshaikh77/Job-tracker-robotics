"""Lever Postings API.

Public endpoint:
  GET https://api.lever.co/v0/postings/{slug}?mode=json

Used by: Canva, Netflix, some startups.
"""
from __future__ import annotations
import logging
from ..models import Job
from .base import Fetcher, register

log = logging.getLogger(__name__)


@register
class LeverFetcher(Fetcher):
    name = "lever"

    def fetch(self, company: dict) -> list[Job]:
        slug = company["slug"]
        url = f"https://api.lever.co/v0/postings/{slug}?mode=json"
        data = self._get_json(url)
        jobs = []
        for j in data:
            categories = j.get("categories") or {}
            loc = categories.get("location", "") or ""
            # Lever sometimes nests location in 'allLocations'
            if not loc and "allLocations" in categories:
                loc = ", ".join(categories["allLocations"][:3])
            desc = j.get("descriptionPlain") or _strip_html(j.get("description", ""))
            jobs.append(Job(
                company=company["name"],
                job_id=str(j["id"]),
                title=j.get("text", ""),
                location=loc,
                url=j.get("hostedUrl", ""),
                description=desc,
                posted_at=str(j.get("createdAt", "")),
            ))
        return jobs


def _strip_html(s: str) -> str:
    import html
    import re
    if not s:
        return ""
    s = re.sub(r"<[^>]+>", " ", s)
    s = html.unescape(s)
    return re.sub(r"\s+", " ", s).strip()
