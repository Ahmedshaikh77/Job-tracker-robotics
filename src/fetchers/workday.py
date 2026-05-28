"""Workday Jobs API (CXS).

Pattern - each tenant has their own host + tenant + site:
  POST https://{host}/wday/cxs/{tenant}/{site}/jobs
  body: {"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": ""}

Job detail URL:
  https://{host}/en-US/{site}{externalPath}

Used by: NVIDIA, Salesforce, Adobe, PayPal, Mastercard, Capital One, BlackRock,
American Express, Morgan Stanley, MSCI, S&P Global, Etsy, PwC, KPMG, J&J, Merck, ...

Tenant/site values vary per company - get them by visiting the careers page and
checking the URL.
"""
from __future__ import annotations
import logging
import time
from ..models import Job
from .base import Fetcher, register

log = logging.getLogger(__name__)


@register
class WorkdayFetcher(Fetcher):
    name = "workday"

    def fetch(self, company: dict) -> list[Job]:
        host = company["host"]
        tenant = company["tenant"]
        site = company["site"]
        max_pages = company.get("max_pages", 5)  # 5 * 20 = 100 newest jobs per company

        endpoint = f"https://{host}/wday/cxs/{tenant}/{site}/jobs"
        # Build URL prefix for job links - Workday returns externalPath, we prepend host/locale/site.
        # Path format that works for most tenants:
        url_prefix = f"https://{host}/en-US/{site}"

        jobs: list[Job] = []
        offset = 0
        limit = 20

        # Allow optional searchText narrowing (per company in config)
        search_terms = company.get("search_terms", [""])

        for term in search_terms:
            offset = 0
            for _ in range(max_pages):
                body = {
                    "appliedFacets": {},
                    "limit": limit,
                    "offset": offset,
                    "searchText": term or "",
                }
                try:
                    data = self._post_json(
                        endpoint,
                        json=body,
                        headers={
                            "Accept": "application/json",
                            "Content-Type": "application/json",
                        },
                    )
                except Exception as e:
                    log.warning("Workday %s page %d failed: %s", company["name"], offset, e)
                    break
                postings = data.get("jobPostings", [])
                if not postings:
                    break
                for p in postings:
                    ext_path = p.get("externalPath", "")
                    jobs.append(Job(
                        company=company["name"],
                        job_id=p.get("bulletFields", [""])[0] or ext_path or p.get("title", ""),
                        title=p.get("title", ""),
                        location=p.get("locationsText", ""),
                        url=f"{url_prefix}{ext_path}",
                        description="",  # Workday list endpoint doesn't return description
                        posted_at=p.get("postedOn", ""),
                    ))
                offset += limit
                if offset >= data.get("total", 0):
                    break
                time.sleep(0.2)  # gentle rate limit
        # Dedupe by job_id within this company (search terms can overlap)
        seen = set()
        unique = []
        for j in jobs:
            if j.job_id in seen:
                continue
            seen.add(j.job_id)
            unique.append(j)
        return unique
