"""SmartRecruiters Postings API.

Public endpoint:
  GET https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=100

Used by: Visa, some others.
"""
from __future__ import annotations
import logging
from ..models import Job
from .base import Fetcher, register

log = logging.getLogger(__name__)


@register
class SmartRecruitersFetcher(Fetcher):
    name = "smartrecruiters"

    def fetch(self, company: dict) -> list[Job]:
        slug = company["slug"]
        url = f"https://api.smartrecruiters.com/v1/companies/{slug}/postings"
        params = {"limit": 100, "offset": 0}
        jobs = []
        for _ in range(5):  # up to 500 jobs
            data = self._get_json(url, params=params)
            items = data.get("content", [])
            if not items:
                break
            for j in items:
                loc_obj = j.get("location") or {}
                loc = ", ".join(filter(None, [loc_obj.get("city"), loc_obj.get("region"), loc_obj.get("country")]))
                jobs.append(Job(
                    company=company["name"],
                    job_id=str(j.get("id", "")),
                    title=j.get("name", ""),
                    location=loc,
                    url=j.get("ref", "") or j.get("postingUrl", ""),
                    description="",  # need separate call for full description
                    posted_at=j.get("releasedDate", ""),
                ))
            params["offset"] += params["limit"]
            if params["offset"] >= data.get("totalFound", 0):
                break
        return jobs
