"""Ashby Jobs API.

Public REST endpoint:
  GET https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=false

Used by: OpenAI, Perplexity, Sierra AI, Linear, Notion (some), and many AI startups.
"""
from __future__ import annotations
import logging
from ..models import Job
from .base import Fetcher, register

log = logging.getLogger(__name__)


@register
class AshbyFetcher(Fetcher):
    name = "ashby"

    def fetch(self, company: dict) -> list[Job]:
        slug = company["slug"]
        url = f"https://api.ashbyhq.com/posting-api/job-board/{slug}"
        data = self._get_json(url, params={"includeCompensation": "false"})
        jobs = []
        for j in data.get("jobs", []):
            jobs.append(Job(
                company=company["name"],
                job_id=str(j.get("id", "")),
                title=j.get("title", ""),
                location=j.get("locationName", "") or j.get("location", ""),
                url=j.get("jobUrl", "") or j.get("applyUrl", ""),
                description=j.get("descriptionPlain", "") or _strip_html(j.get("descriptionHtml", "")),
                posted_at=j.get("publishedAt", ""),
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
