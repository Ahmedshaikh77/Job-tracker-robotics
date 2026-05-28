"""Greenhouse Job Board API.

Public endpoint, no auth needed:
  GET https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true

Used by: Anthropic, Stripe, Databricks, Snowflake, Figma, Notion, Cohere, Two Sigma, ...
"""
from __future__ import annotations
import logging
from ..models import Job
from .base import Fetcher, register

log = logging.getLogger(__name__)


@register
class GreenhouseFetcher(Fetcher):
    name = "greenhouse"

    def fetch(self, company: dict) -> list[Job]:
        slug = company["slug"]
        url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"
        data = self._get_json(url)
        jobs = []
        for j in data.get("jobs", []):
            loc = (j.get("location") or {}).get("name", "")
            jobs.append(Job(
                company=company["name"],
                job_id=str(j["id"]),
                title=j.get("title", ""),
                location=loc,
                url=j.get("absolute_url", ""),
                description=_strip_html(j.get("content", "")),
                posted_at=j.get("updated_at"),
            ))
        return jobs


def _strip_html(s: str) -> str:
    """Very lightweight HTML strip for filter matching. Doesn't need to be perfect."""
    import html
    import re
    if not s:
        return ""
    s = re.sub(r"<[^>]+>", " ", s)
    s = html.unescape(s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()
