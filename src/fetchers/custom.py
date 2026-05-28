"""Custom per-company fetchers - for companies that don't use a standard ATS.

Each of these reverse-engineers the JSON endpoint the company's careers site uses
internally. These can break if the company changes their site - watch the logs.
"""
from __future__ import annotations
import logging
from urllib.parse import quote
from ..models import Job
from .base import Fetcher, register

log = logging.getLogger(__name__)


@register
class AmazonFetcher(Fetcher):
    name = "amazon"

    def fetch(self, company: dict) -> list[Job]:
        # amazon.jobs internal search endpoint
        url = "https://www.amazon.jobs/en/search.json"
        params = {
            "result_limit": 100,
            "sort": "recent",
            "country[]": "USA",
            # category teams the user cares about - broad enough to catch DS/AI/ML
            "category[]": ["software-development", "machine-learning-science", "data-engineering",
                           "business-intelligence", "research-science", "solutions-architect"],
        }
        # Allow override via config
        if "search_query" in company:
            params["base_query"] = company["search_query"]
        data = self._get_json(url, params=params)
        jobs = []
        for j in data.get("jobs", []):
            jobs.append(Job(
                company="Amazon",
                job_id=str(j.get("id_icims") or j.get("id", "")),
                title=j.get("title", ""),
                location=j.get("normalized_location", "") or j.get("location", ""),
                url=f"https://www.amazon.jobs{j.get('job_path', '')}",
                description=j.get("description_short", "") or j.get("description", ""),
                posted_at=j.get("posted_date", ""),
            ))
        return jobs


@register
class GoogleFetcher(Fetcher):
    name = "google"

    def fetch(self, company: dict) -> list[Job]:
        # careers.google.com uses a JSON API; returns paginated results
        base = "https://www.google.com/about/careers/applications/jobs/results"
        jobs = []
        # Query Google's careers via the internal jobs endpoint
        # The endpoint format below is the one used by the careers.google.com SPA
        url = "https://careers.google.com/api/v3/search/"
        for page in range(1, 6):  # up to ~100 jobs
            params = {
                "page": page,
                "page_size": 20,
                "sort_by": "date",
                "employment_type": "FULL_TIME",
                "locations.country_code": "US",
            }
            if "search_query" in company:
                params["q"] = company["search_query"]
            try:
                data = self._get_json(url, params=params)
            except Exception as e:
                log.warning("Google fetcher page %d failed: %s", page, e)
                break
            items = data.get("jobs", []) or data.get("results", [])
            if not items:
                break
            for j in items:
                locs = j.get("locations", []) or [{}]
                loc_str = locs[0].get("display") if locs else ""
                jid = j.get("id", "") or j.get("job_id", "")
                jobs.append(Job(
                    company="Google",
                    job_id=str(jid),
                    title=j.get("title", ""),
                    location=loc_str or "",
                    url=f"https://careers.google.com/jobs/results/{jid}/",
                    description=j.get("description", "") or j.get("summary", ""),
                    posted_at=j.get("publish_date", "") or j.get("created", ""),
                ))
        return jobs


@register
class MicrosoftFetcher(Fetcher):
    name = "microsoft"

    def fetch(self, company: dict) -> list[Job]:
        # Microsoft careers uses the gcsservices endpoint
        url = "https://gcsservices.careers.microsoft.com/search/api/v1/search"
        jobs = []
        for page in range(1, 6):
            params = {
                "l": "en_us",
                "pg": page,
                "pgSz": 20,
                "lc": "United States",
                "o": "Recent",
                "flt": "true",
            }
            if "search_query" in company:
                params["q"] = company["search_query"]
            try:
                data = self._get_json(url, params=params)
            except Exception as e:
                log.warning("Microsoft fetcher page %d failed: %s", page, e)
                break
            result = (data.get("operationResult") or {}).get("result") or {}
            items = result.get("jobs", [])
            if not items:
                break
            for j in items:
                loc = ", ".join(j.get("properties", {}).get("locations", []) or [j.get("primaryLocation", "")])
                jid = j.get("jobId", "")
                jobs.append(Job(
                    company="Microsoft",
                    job_id=str(jid),
                    title=j.get("title", ""),
                    location=loc,
                    url=f"https://jobs.careers.microsoft.com/global/en/job/{jid}",
                    description=j.get("properties", {}).get("description", "") or "",
                    posted_at=j.get("postingDate", ""),
                ))
        return jobs


@register
class AppleFetcher(Fetcher):
    name = "apple"

    def fetch(self, company: dict) -> list[Job]:
        # Apple's jobs search returns JSON via POST
        url = "https://jobs.apple.com/api/role/search"
        jobs = []
        for page in range(1, 6):
            body = {
                "query": company.get("search_query", ""),
                "filters": {
                    "range": {"standardWeeklyHours": {"start": None, "end": None}},
                    "postingpostLocation": ["postLocation-USA"],
                },
                "page": page,
                "locale": "en-us",
                "sort": "newest",
            }
            try:
                data = self._post_json(url, json=body)
            except Exception as e:
                log.warning("Apple fetcher page %d failed: %s", page, e)
                break
            items = data.get("searchResults", []) or data.get("res", {}).get("searchResults", [])
            if not items:
                break
            for j in items:
                pid = j.get("positionId", "") or j.get("id", "")
                loc = j.get("locations", [{}])
                loc_str = ", ".join(l.get("name", "") for l in loc) if isinstance(loc, list) else ""
                jobs.append(Job(
                    company="Apple",
                    job_id=str(pid),
                    title=j.get("postingTitle", "") or j.get("title", ""),
                    location=loc_str,
                    url=f"https://jobs.apple.com/en-us/details/{pid}",
                    description=j.get("jobSummary", "") or "",
                    posted_at=j.get("postingDate", "") or j.get("postDateInGMT", ""),
                ))
        return jobs


@register
class MetaFetcher(Fetcher):
    name = "meta"

    def fetch(self, company: dict) -> list[Job]:
        # Meta careers (metacareers.com) uses a GraphQL endpoint
        url = "https://www.metacareers.com/graphql"
        jobs = []
        # Variables roughly matching the careers SPA. The doc_id changes occasionally;
        # if this breaks, inspect a fresh request in your browser devtools and update.
        variables = {
            "search_input": {
                "q": company.get("search_query", ""),
                "divisions": [],
                "offices": ["United States"],
                "roles": [],
                "leadership_levels": [],
                "saved_jobs": [],
                "saved_searches": [],
                "sub_teams": [],
                "teams": [],
                "is_leadership": False,
                "is_remote_only": False,
                "is_in_page": False,
            }
        }
        try:
            r = self.session.post(
                url,
                data={
                    "variables": __import__("json").dumps(variables),
                    "doc_id": "9114524511922157",  # current as of build; may need update
                },
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Accept": "*/*",
                },
                timeout=self.timeout,
            )
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            log.warning("Meta fetcher failed: %s", e)
            return []
        results = ((data.get("data") or {}).get("job_search") or [])
        for j in results:
            jid = j.get("id", "")
            jobs.append(Job(
                company="Meta",
                job_id=str(jid),
                title=j.get("title", ""),
                location=", ".join(j.get("locations", [])),
                url=f"https://www.metacareers.com/jobs/{jid}/",
                description="",
                posted_at="",
            ))
        return jobs


@register
class IbmFetcher(Fetcher):
    name = "ibm"

    def fetch(self, company: dict) -> list[Job]:
        # IBM's public careers API
        url = "https://www-api.ibm.com/search/v1/search/jobs/_search"
        jobs = []
        for offset in (0, 25, 50, 75):
            body = {
                "appId": "careers",
                "scopes": ["jobs"],
                "query": {
                    "bool": {
                        "must": [
                            {"match": {"country": "United States"}},
                        ]
                    }
                },
                "from": offset,
                "size": 25,
                "sort": [{"open_date": {"order": "desc"}}],
            }
            try:
                data = self._post_json(url, json=body)
            except Exception as e:
                log.warning("IBM fetcher offset %d failed: %s", offset, e)
                break
            hits = (data.get("hits") or {}).get("hits", [])
            if not hits:
                break
            for h in hits:
                src = h.get("_source", {})
                jid = src.get("id") or h.get("_id", "")
                jobs.append(Job(
                    company="IBM",
                    job_id=str(jid),
                    title=src.get("title", ""),
                    location=src.get("location", "") or src.get("city", ""),
                    url=src.get("apply_url", "") or f"https://www.ibm.com/careers/job/{jid}",
                    description=src.get("description", ""),
                    posted_at=src.get("open_date", ""),
                ))
        return jobs


@register
class BloombergFetcher(Fetcher):
    name = "bloomberg"

    def fetch(self, company: dict) -> list[Job]:
        # Bloomberg careers uses a public listing API
        url = "https://careers.bloomberg.com/api/job-search"
        params = {
            "country": "United States",
            "limit": 100,
            "offset": 0,
        }
        if "search_query" in company:
            params["q"] = company["search_query"]
        try:
            data = self._get_json(url, params=params)
        except Exception as e:
            log.warning("Bloomberg fetcher failed: %s", e)
            return []
        jobs = []
        for j in data.get("jobs", []) or data.get("results", []):
            jid = j.get("id", "") or j.get("requisitionId", "")
            jobs.append(Job(
                company="Bloomberg",
                job_id=str(jid),
                title=j.get("title", ""),
                location=j.get("location", "") or ", ".join(j.get("locations", [])),
                url=j.get("url", "") or f"https://careers.bloomberg.com/job/detail/{jid}",
                description=j.get("description", ""),
                posted_at=j.get("postedDate", ""),
            ))
        return jobs


@register
class DeloitteFetcher(Fetcher):
    name = "deloitte"

    def fetch(self, company: dict) -> list[Job]:
        url = "https://apply.deloitte.com/api/jobs/search"
        params = {
            "country": "United States",
            "limit": 100,
            "offset": 0,
        }
        try:
            data = self._get_json(url, params=params)
        except Exception as e:
            log.warning("Deloitte fetcher failed: %s", e)
            return []
        jobs = []
        for j in data.get("jobs", []):
            jid = j.get("jobId") or j.get("id", "")
            jobs.append(Job(
                company="Deloitte",
                job_id=str(jid),
                title=j.get("title", ""),
                location=j.get("location", "") or j.get("city", ""),
                url=f"https://apply.deloitte.com/en_US/careers/JobDetail/{jid}",
                description=j.get("description", ""),
                posted_at=j.get("postedDate", ""),
            ))
        return jobs


@register
class AccentureFetcher(Fetcher):
    name = "accenture"

    def fetch(self, company: dict) -> list[Job]:
        # Accenture careers API
        url = "https://www.accenture.com/api/accenture/jobsearch/result"
        params = {
            "pageNumber": 1,
            "pageSize": 50,
            "countryName": "United States of America",
            "sortBy": "MOST_RECENT",
        }
        if "search_query" in company:
            params["jobKeyword"] = company["search_query"]
        try:
            data = self._get_json(url, params=params)
        except Exception as e:
            log.warning("Accenture fetcher failed: %s", e)
            return []
        jobs = []
        for j in data.get("data", []) or data.get("jobs", []):
            jid = j.get("jobId", "") or j.get("id", "")
            jobs.append(Job(
                company="Accenture",
                job_id=str(jid),
                title=j.get("title", "") or j.get("jobTitle", ""),
                location=j.get("jobLocation", "") or j.get("city", ""),
                url=j.get("jobDetailUrl", "") or f"https://www.accenture.com/us-en/careers/jobdetails?id={jid}",
                description=j.get("description", "") or "",
                posted_at=j.get("postedDate", ""),
            ))
        return jobs
