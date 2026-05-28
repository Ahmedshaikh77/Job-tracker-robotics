"""Oracle Cloud HCM Recruiting (Oracle Recruiting Cloud).

Pattern - each tenant has its own host + site:
  GET https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions?...

Used by: JPMorgan Chase (jpmc.fa.oraclecloud.com), Oracle itself.
"""
from __future__ import annotations
import logging
from ..models import Job
from .base import Fetcher, register

log = logging.getLogger(__name__)


@register
class OracleCloudFetcher(Fetcher):
    name = "oracle_cloud"

    def fetch(self, company: dict) -> list[Job]:
        host = company["host"]
        site_id = company["site_number"]  # e.g. "CX_1001" for JPMC
        limit = 25
        offset = 0
        jobs: list[Job] = []
        url = f"https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions"
        # The "finder" param selects job-search behavior; we use it without filters to get all postings.
        finder = (
            f"findReqs;siteNumber={site_id},facetsList=LOCATIONS;TITLES;CATEGORIES,"
            f"limit={limit},sortBy=POSTING_DATES_DESC"
        )

        public_url_base = company.get("public_url_base", f"https://{host}/?lang=en")

        for _ in range(8):  # up to 200 newest jobs
            params = {
                "onlyData": "true",
                "expand": "requisitionList.secondaryLocations,flexFieldsFacet.values",
                "finder": f"{finder},offset={offset}",
            }
            try:
                data = self._get_json(url, params=params, headers={"Accept": "application/json"})
            except Exception as e:
                log.warning("Oracle Cloud %s offset %d failed: %s", company["name"], offset, e)
                break
            items = (data.get("items") or [{}])[0].get("requisitionList", []) if data.get("items") else []
            if not items:
                break
            for j in items:
                req_id = j.get("Id") or j.get("RequisitionId", "")
                jobs.append(Job(
                    company=company["name"],
                    job_id=str(req_id),
                    title=j.get("Title", ""),
                    location=j.get("PrimaryLocation", ""),
                    url=f"{public_url_base}&job={req_id}" if "?" in public_url_base else f"{public_url_base}?job={req_id}",
                    description="",
                    posted_at=j.get("PostedDate", ""),
                ))
            offset += limit
            total = (data.get("items") or [{}])[0].get("TotalJobsCount", 0)
            if offset >= total:
                break
        return jobs
