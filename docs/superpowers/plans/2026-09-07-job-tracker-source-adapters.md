# Job Tracker Source Adapters Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace stale or incomplete company integrations with complete, testable adapters for the approved robotics-company roster.

**Architecture:** Each adapter implements the shared `Fetcher` contract, returns an explicit `FetchResult`, and performs detail enrichment only for Stage-1 candidates. Documented ATS feeds are primary, de facto public backends are schema-guarded, and Tesla fails closed when bot protection prevents a trustworthy snapshot.

**Tech Stack:** Python 3.11, requests, dataclasses, pytest, JSON fixtures

**Spec:** `docs/superpowers/specs/2026-09-07-job-tracker-alerts-v2-design.md`

## Global Constraints

- Complete enumeration is mandatory. A failed page makes the whole result partial and preserves prior active IDs.
- A 304 result is complete and unchanged, and returns the prior active IDs from `FetchContext`.
- Official application URLs are retained. Search-engine results are never a source of record.
- Every adapter normalizes available city, region, ISO country code, employment type, workplace type, base compensation, and official application URL, with per-field provenance. Missing structured facts remain unknown rather than guessed from company defaults.
- Workday, Rippling, Amazon, and Tesla adapters must validate their response schema and never convert 403, HTML, empty malformed data, or a partial page into a healthy zero-job snapshot.
- Tesla is best-effort and carries no 15-to-45-minute guarantee while the official endpoint blocks automated clients.
- Do not send Telegram messages. Network behavior is tested with fixtures and fake HTTP clients.

**Prerequisite:** Complete `2026-09-07-job-tracker-core-foundation.md` first.

---

### Task 1: Convert Greenhouse, Ashby, and Lever to complete feed results

**Files:**
- Modify: `src/fetchers/greenhouse.py:1-49`
- Modify: `src/fetchers/ashby.py:1-45`
- Modify: `src/fetchers/lever.py:1-49`
- Create: `tests/fixtures/greenhouse_jobs.json`
- Create: `tests/fixtures/greenhouse_job_detail.json`
- Create: `tests/fixtures/ashby_jobs.json`
- Create: `tests/fixtures/lever_jobs_page_1.json`
- Create: `tests/fixtures/lever_jobs_page_2.json`
- Create: `tests/fixtures/lever_job_detail.json`
- Create: `tests/test_fetchers_tier_a.py`

**Interfaces:**
- Consumes: `Fetcher.fetch(company, context) -> FetchResult`, `HttpClient`, `FetchContext`, `FetchHealth`, `Job`, `SalaryRange`, `EmploymentType`, `WorkplaceType`, and `PayPeriod`
- Produces: complete normalized results and explicit fail-closed `fetch_detail()` implementations for `greenhouse`, `ashby`, and `lever`

- [ ] **Step 1: Add minimal sanitized fixtures**

Use fixtures containing these exact distinguishing fields:

```json
{
  "meta": {"total": 1},
  "jobs": [
    {
      "id": 123,
      "title": "Robotics Test Engineer",
      "location": {"name": "Sunnyvale, CA"},
      "absolute_url": "https://boards.greenhouse.io/figureai/jobs/123",
      "content": "<p>Full-time role building validation fixtures.</p>",
      "updated_at": "2026-09-07T10:00:00-04:00"
    }
  ]
}
```

```json
{
  "jobs": [
    {
      "id": "ashby-1",
      "title": "Hardware Integration Engineer",
      "location": "Mountain View, CA",
      "jobUrl": "https://jobs.ashbyhq.com/applied/ashby-1",
      "descriptionPlain": "Full-time integration and validation.",
      "publishedAt": "2026-09-07T14:00:00.000Z",
      "employmentType": "FullTime",
      "workplaceType": "OnSite",
      "compensation": {
        "summaryComponents": [
          {"compensationType": "Salary", "minValue": 120000, "maxValue": 180000, "currencyCode": "USD", "interval": "1 YEAR"}
        ],
        "compensationTiers": []
      }
    }
  ]
}
```

```json
[
  {
    "id": "lever-1",
    "text": "Autonomy System Test Engineer",
    "hostedUrl": "https://jobs.lever.co/zoox/lever-1",
    "applyUrl": "https://jobs.lever.co/zoox/lever-1/apply",
    "descriptionPlain": "Full-time system test role requiring 2 years of experience.",
    "createdAt": 1788789600000,
    "country": "US",
    "workplaceType": "onsite",
    "salaryRange": {"min": 120000, "max": 170000, "currency": "USD", "interval": "per-year-salary"},
    "categories": {"location": "Foster City, CA", "allLocations": ["Foster City, CA"], "commitment": "Full-time", "team": "Systems Test"},
    "lists": [{"text": "Requirements", "content": "2 years of experience"}],
    "additional": "Must be authorized to work at hire."
  }
]
```

Set `lever_jobs_page_2.json` to an empty JSON array so completion is explicit.

Use this exact Greenhouse detail fixture:

```json
{
  "id": 123,
  "title": "Robotics Test Engineer",
  "location": {"name": "Sunnyvale, CA"},
  "absolute_url": "https://boards.greenhouse.io/figureai/jobs/123",
  "content": "<p>Full-time role building validation fixtures.</p>",
  "first_published": "2026-09-05T09:00:00-04:00",
  "updated_at": "2026-09-07T10:00:00-04:00"
}
```

Use this exact Lever detail fixture so detail URL and field precedence are executable rather than prose-only:

```json
{
  "id": "lever-1",
  "text": "Autonomy System Test Engineer",
  "hostedUrl": "https://jobs.lever.co/zoox/lever-1",
  "applyUrl": "https://jobs.lever.co/zoox/lever-1/apply",
  "descriptionPlain": "Full-time system test role requiring 2 years of experience.",
  "createdAt": 1788789600000,
  "country": "US",
  "workplaceType": "onsite",
  "salaryRange": {"min": 120000, "max": 170000, "currency": "USD", "interval": "per-year-salary"},
  "categories": {"location": "Foster City, CA", "allLocations": ["Foster City, CA"], "commitment": "Full-time", "team": "Systems Test"},
  "lists": [{"text": "Requirements", "content": "2 years of experience"}],
  "additional": "Must be authorized to work at hire."
}
```

- [ ] **Step 2: Write failing normalization, ETag, and pagination tests**

In `tests/test_fetchers_tier_a.py`, define a module-local `fake_http(request)` fixture using `FakeHttp` and `load_json_fixture()` from `tests/fakes.py`. A `HAPPY_PATHS` mapping keyed by the exact test function name supplies only that test's ordered responses: Greenhouse list, Ashby board, or Lever page 1/page 2. Alternate error and 304 tests construct a fresh `FakeHttp` directly, so queues cannot leak between tests. Assert:

```python
def test_greenhouse_uses_update_only_as_updated_at(fake_http, fetch_context):
    result = GreenhouseFetcher(fake_http).fetch(
        {"name": "Figure", "fetcher": "greenhouse", "slug": "figureai"},
        fetch_context,
    )
    job = result.jobs[0]
    assert result.complete is True
    assert result.total_is_authoritative is True
    assert result.active_ids == frozenset({"123"})
    assert job.posted_at is None
    assert job.updated_at == "2026-09-07T10:00:00-04:00"


def test_ashby_keeps_compensation_and_full_time(fake_http, fetch_context):
    company = {"name": "Applied Intuition", "fetcher": "ashby", "slug": "applied"}
    fetcher = AshbyFetcher(fake_http)
    result = fetcher.fetch(company, fetch_context)
    job = result.jobs[0]
    assert job.employment_type is EmploymentType.FULL_TIME
    assert job.workplace_type is WorkplaceType.ONSITE
    assert job.salary.annual_minimum == Decimal("120000")
    assert result.total_is_authoritative is False
    assert fetcher.fetch_detail(company, job).status is DetailStatus.HEALTHY


def test_lever_reads_until_short_page_and_uses_fingerprint(fake_http, fetch_context, monkeypatch):
    monkeypatch.setattr(LeverFetcher, "PAGE_SIZE", 1)
    result = LeverFetcher(fake_http).fetch(
        {"name": "Zoox", "fetcher": "lever", "slug": "zoox"},
        fetch_context,
    )
    assert result.pages_fetched == 2
    assert result.complete is True
    assert result.total_is_authoritative is False
    assert result.fingerprint is not None
```

Add a second fake sequence where Lever page 2 raises a 503 and assert `health == FetchHealth.PARTIAL`, `complete is False`, and `active_ids == fetch_context.previous_active_ids`. Add a 304 Greenhouse case and assert the prior active IDs are returned with `unchanged=True`. Add `test_greenhouse_total_mismatch_is_partial`: a present `meta.total` is authoritative, so `jobs=[]` with `meta.total=1` is partial, not empty-valid. A complete empty board requires `meta.total == 0`.

Add explicit detail tests for all three adapters. Greenhouse and Lever cover healthy, official 404, official 410, and transient/403 failure. Ashby's board response is the complete official posting record, so its adapter-specific `fetch_detail()` may certify only a job marked `detail_complete=True` by the just-validated full board record; a hand-built/unmarked job or missing required full-record field returns `FAILED`. Also prove `FetchContext.force_full=True` suppresses ETag use for Greenhouse and Ashby. Lever pagination adds duplicate-ID and repeated-page failures so it cannot loop or report a complete duplicate inventory.

- [ ] **Step 3: Run tests and verify they fail against list-returning adapters**

```bash
python -m pytest tests/test_fetchers_tier_a.py -v
```

Expected: failures because current adapters return `list[Job]`, omit source identity, and do not expose completeness or compensation.

- [ ] **Step 4: Implement the three adapters with exact endpoint behavior**

Use these endpoint and mapping rules:

| Adapter | Request | Pagination/completion | Posted semantics |
| --- | --- | --- | --- |
| Greenhouse | `GET https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true` | One object containing `meta.total` and a `jobs` list; mismatch is partial; only explicit total zero is empty-valid | List `updated_at` goes to `updated_at`; detail `first_published` goes to `posted_at` |
| Ashby | `GET https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true` | One object containing a `jobs` list; absent list is failed | `publishedAt` is source-posted with structured-feed provenance |
| Lever | `GET https://api.lever.co/v0/postings/{slug}?mode=json&skip={offset}&limit={PAGE_SIZE}` with `PAGE_SIZE = 100` | Continue until a page has fewer than `PAGE_SIZE` rows; any page failure makes the result partial | Preserve `createdAt` only in metadata; leave `posted_at=None` and use first-seen freshness |

Every successful adapter computes `active_ids` from posting IDs. Greenhouse and Ashby pass `context.previous_etag` only when `context.force_full` is false and handle 304. Lever computes SHA-256 over canonicalized complete posting content, not IDs alone, because stable IDs must not hide material revisions. It uses the digest only as change evidence and never returns `unchanged=True` without a complete record comparison. Map Lever `country`, `workplaceType`, `salaryRange`, `applyUrl`, `allLocations`, `lists`, and `additional`; the official application URL is `applyUrl` when present, otherwise `hostedUrl`. HTML stripping must use one shared helper in `src/fetchers/base.py`, not duplicate regular expressions. Map raw employment, workplace, and pay intervals into the shared enums; store raw values only in metadata and never invent USD.

For the validated document-style feeds, an explicit empty collection at the normal collection key is the source's zero-inventory assertion: Ashby `{ "jobs": [] }` and a first Lever page of `[]` return `EMPTY_VALID`, `source_total=0`, and `total_is_authoritative=True`. A missing collection, malformed response, empty later page after an unreconciled failure, or any unexplained empty body is failed or partial and preserves prior active IDs. Nonempty Ashby and Lever inventories use their observed job count with `total_is_authoritative=False` because neither response exposes a separate total.

- [ ] **Step 5: Add explicit detail verification for every Tier-A adapter**

Implement `GreenhouseFetcher.fetch_detail()` using:

```python
url = f"https://boards-api.greenhouse.io/v1/boards/{company['slug']}/jobs/{job.job_id}"
payload = self.http.get_json(url).data
if not isinstance(payload, dict) or str(payload.get("id")) != job.job_id or not payload.get("content"):
    return DetailResult(
        job=None,
        status=DetailStatus.FAILED,
        fetched_at=datetime.now(timezone.utc).isoformat(),
        error="Greenhouse detail schema mismatch",
    )
enriched = replace(
    job,
    description=strip_html(payload.get("content", job.description)),
    posted_at=payload.get("first_published") or job.posted_at,
    updated_at=payload.get("updated_at") or job.updated_at,
    provenance={
        **job.provenance,
        "description": FactSource.OFFICIAL_DETAIL,
        **({"posted_at": FactSource.OFFICIAL_DETAIL} if payload.get("first_published") else {}),
        **({"updated_at": FactSource.OFFICIAL_DETAIL} if payload.get("updated_at") else {}),
    },
)
return DetailResult(
    job=enriched,
    status=DetailStatus.HEALTHY,
    fetched_at=datetime.now(timezone.utc).isoformat(),
)
```

Require matching ID and a valid nonempty content shape before healthy status. Promote provenance to `OFFICIAL_DETAIL` only for a field actually present in the detail payload; a fallback list value keeps its existing provenance. Map an official 404 or 410 to `DetailStatus.CLOSED`. Map timeouts, 403, 429, 5xx, HTML, invalid JSON, and schema mismatch to `DetailStatus.FAILED`. Detail failure does not change the complete roster result, but the candidate is withheld and retried.

Implement Lever detail with the official per-post endpoint:

```python
url = f"https://api.lever.co/v0/postings/{company['slug']}/{job.job_id}?mode=json"
payload = self.http.get_json(url).data
```

Require the returned ID to equal `job.job_id`, then replace description, categories, provenance, and the application URL from the detail object. Use `applyUrl` when it is present and valid; fall back to `hostedUrl` only when `applyUrl` is absent. Apply the same 404/410/failed mapping as Greenhouse.

Implement `AshbyFetcher.fetch_detail()` explicitly rather than inheriting the failing base method. Because the official board record already contains the full posting body, the list parser sets private metadata `detail_complete=True` only after validating the response shape plus nonempty ID, title, official URL, and description. Location, employment type, and publication date are optional facts: validate and normalize them when present, leave them unknown with unavailable provenance when absent, and never fail an otherwise valid full record merely because one is absent. The detail method checks the marker and required provenance, returns a copy whose description provenance is `OFFICIAL_DETAIL`, and otherwise returns `FAILED`. It never certifies an arbitrary `Job` created outside the accepted board snapshot.

- [ ] **Step 6: Run tests and commit**

```bash
python -m pytest tests/test_fetchers_tier_a.py -v
git add src/fetchers/base.py src/fetchers/greenhouse.py src/fetchers/ashby.py src/fetchers/lever.py tests/fixtures tests/test_fetchers_tier_a.py
git commit -m "feat: repair primary ATS adapters"
```

Expected: all Tier-A adapter tests pass before the commit.

---

### Task 2: Implement SmartRecruiters and Gem with detail-ready records

**Files:**
- Modify: `src/fetchers/smartrecruiters.py:1-48`
- Create: `src/fetchers/gem.py`
- Modify: `src/fetchers/__init__.py:1-11`
- Create: `tests/fixtures/smartrecruiters_page_1.json`
- Create: `tests/fixtures/smartrecruiters_page_2.json`
- Create: `tests/fixtures/smartrecruiters_detail.json`
- Create: `tests/fixtures/gem_jobs.json`
- Create: `tests/fixtures/gem_job_detail.json`
- Create: `tests/test_fetchers_smartrecruiters_gem.py`

**Interfaces:**
- Consumes: shared fetch and enrichment contracts
- Produces: `SmartRecruitersFetcher` and `GemFetcher` with complete pagination and explicit detail enrichment

- [ ] **Step 1: Write failing contract tests**

Define a fresh module-local `fake_http(request)` fixture with a test-name-to-response mapping: the SmartRecruiters test receives page 1, page 2, and detail in order; the Gem test receives its list and detail in order. Add fixtures and tests using these fields:

Write these exact sanitized fixture bodies. `smartrecruiters_page_1.json` is:

```json
{"totalFound": 2, "content": [{"id": "sr-1", "name": "Robotics Systems Engineer I", "releasedDate": "2026-09-05T12:00:00Z", "location": {"city": "Sunnyvale", "region": "CA", "country": "us"}, "typeOfEmployment": {"id": "FULL_TIME", "label": "Full-time"}, "experienceLevel": {"id": "ENTRY_LEVEL", "label": "Entry level"}, "ref": "https://api.smartrecruiters.com/v1/companies/Intuitive/postings/sr-1"}]}
```

`smartrecruiters_page_2.json` differs in identity and contains:

```json
{"totalFound": 2, "content": [{"id": "sr-2", "name": "Mechatronics Engineer I", "releasedDate": "2026-09-04T12:00:00Z", "location": {"city": "Sunnyvale", "region": "CA", "country": "us"}, "typeOfEmployment": {"id": "FULL_TIME", "label": "Full-time"}, "experienceLevel": {"id": "ASSOCIATE", "label": "Associate"}, "ref": "https://api.smartrecruiters.com/v1/companies/Intuitive/postings/sr-2"}]}
```

`smartrecruiters_detail.json` is:

```json
{"id": "sr-1", "name": "Robotics Systems Engineer I", "applyUrl": "https://jobs.smartrecruiters.com/Intuitive/sr-1/apply", "postingUrl": "https://jobs.smartrecruiters.com/Intuitive/sr-1", "releasedDate": "2026-09-05T12:00:00Z", "location": {"city": "Sunnyvale", "region": "CA", "country": "us"}, "typeOfEmployment": {"id": "FULL_TIME", "label": "Full-time"}, "jobAd": {"sections": {"jobDescription": {"text": "Full-time robotics validation role."}, "qualifications": {"text": "Two years of hardware integration experience."}}}}
```

`gem_jobs.json` is:

```json
[{"id": "gem-1", "internal_job_id": "CHEF-101", "title": "Robotics Hardware Test Engineer", "content": "<p>Validate robotic systems.</p>", "content_plain": "Validate robotic systems.", "location": {"name": "San Francisco, CA"}, "location_type": "On-site", "employment_type": "Full-time", "created_at": "2026-09-04T12:00:00Z", "first_published_at": "2026-09-05T12:00:00Z", "updated_at": "2026-09-07T12:00:00Z", "departments": [{"name": "Hardware"}], "offices": [{"name": "San Francisco"}], "absolute_url": "https://jobs.gem.com/chef-robotics/gem-1"}]
```

`gem_job_detail.json` repeats that object as a top-level object, with matching `id`, rather than as a list. Tests mutate one field at a time for ID mismatch, missing content, and missing URL cases; do not introduce alternate undocumented shapes.

```python
def test_smartrecruiters_paginates_to_total_and_enriches_detail(fake_http, fetch_context, monkeypatch):
    monkeypatch.setattr(SmartRecruitersFetcher, "PAGE_SIZE", 1)
    company = {"name": "Intuitive", "fetcher": "smartrecruiters", "slug": "Intuitive"}
    fetcher = SmartRecruitersFetcher(fake_http)
    result = fetcher.fetch(company, fetch_context)
    detail = fetcher.fetch_detail(company, result.jobs[0])
    enriched = detail.job
    assert detail.status is DetailStatus.HEALTHY
    assert result.source_total == 2
    assert result.total_is_authoritative is True
    assert result.pages_fetched == 2
    assert enriched.description == (
        "Full-time robotics validation role.\n\n"
        "Two years of hardware integration experience."
    )
    assert enriched.url == "https://jobs.smartrecruiters.com/Intuitive/sr-1/apply"
    assert enriched.employment_type is EmploymentType.FULL_TIME


def test_gem_maps_authoritative_dates_and_employment_type(fake_http, fetch_context):
    company = {"name": "Chef Robotics", "fetcher": "gem", "slug": "chef-robotics"}
    fetcher = GemFetcher(fake_http)
    result = fetcher.fetch(company, fetch_context)
    job = result.jobs[0]
    assert fetcher.fetch_detail(company, job).status is DetailStatus.HEALTHY
    assert job.posted_at == "2026-09-05T12:00:00Z"
    assert job.updated_at == "2026-09-07T12:00:00Z"
    assert job.employment_type is EmploymentType.FULL_TIME
```

Set `SmartRecruitersFetcher.PAGE_SIZE = 1` with `monkeypatch` in the pagination test so two one-record pages prove total-driven enumeration. The two distinct list fixtures contain `totalFound`, `releasedDate`, structured `location`, `typeOfEmployment`, `experienceLevel`, and `ref`. The detail fixture contains `jobAd.sections.jobDescription.text`, `jobAd.sections.qualifications.text`, `applyUrl`, and `postingUrl`. Join the two nonempty text sections with exactly two newline characters and assert that `applyUrl`, not `postingUrl` or API `ref`, becomes the official application URL. Add a mutation that removes `applyUrl` and assert `postingUrl` is the fallback. The Gem list fixture is a top-level JSON array. Each record contains `id`, `internal_job_id`, `title`, `content`, `content_plain`, `location.name`, `location_type`, `employment_type`, `created_at`, `first_published_at`, `updated_at`, `departments`, `offices`, and `absolute_url`. Assert SmartRecruiters sets `total_is_authoritative=True`. A nonempty Gem array uses its inferred list length with `total_is_authoritative=False`; an explicit empty top-level array is the source's zero-inventory assertion and returns `EMPTY_VALID`, `source_total=0`, and `total_is_authoritative=True`. Add short/final-count mismatch cases for SmartRecruiters. Add healthy, 404, 410, transient, and schema-failure detail cases for SmartRecruiters and Gem.

- [ ] **Step 2: Run tests and verify expected failures**

```bash
python -m pytest tests/test_fetchers_smartrecruiters_gem.py -v
```

Expected: SmartRecruiters lacks detail enrichment and `GemFetcher` is absent.

- [ ] **Step 3: Implement SmartRecruiters pagination and detail enrichment**

Enumerate the complete public board from `https://api.smartrecruiters.com/v1/companies/{company['slug']}/postings` using `destination=PUBLIC`, `PAGE_SIZE = 100`, and increasing `offset`. Do not apply a country filter; Stage 1 handles U.S. eligibility after complete inventory is established. Stop only when `offset >= totalFound` and `len(unique_ids) == totalFound`; an absent/changing total, repeated page, duplicate ID, short or empty page before the total, or final count mismatch makes the result partial. Construct detail URL `https://api.smartrecruiters.com/v1/companies/{company['slug']}/postings/{job.job_id}` instead of issuing a request to arbitrary list data; when `ref` is present, require it to equal that URL. Require the detail identifier to match the list candidate, and join nonempty `jobAd.sections.jobDescription.text` and `jobAd.sections.qualifications.text` with `"\n\n"`. Prefer a valid `applyUrl` for the official application URL and fall back to a valid `postingUrl` only when `applyUrl` is absent. Preserve `releasedDate`, structured employment type, and experience level with per-field provenance.

```python
endpoint = f"https://api.smartrecruiters.com/v1/companies/{company['slug']}/postings"
params = {"destination": "PUBLIC", "limit": self.PAGE_SIZE, "offset": offset}
page = self.http.get_json(endpoint, params=params).data
total = page["totalFound"]
items = page["content"]
```

- [ ] **Step 4: Implement the Gem adapter and per-post detail**

Register `GemFetcher.name = "gem"` and call:

```python
endpoint = f"https://api.gem.com/job_board/v0/{company['slug']}/job_posts/"
etag = None if context.force_full else context.previous_etag
response = self.http.get_json(endpoint, etag=etag)
```

Require a top-level list, map the exact fixture fields, and prefer `content_plain` over stripped HTML. Treat an explicit empty top-level list as `EMPTY_VALID` with `source_total=0` and `total_is_authoritative=True`; nonempty inferred counts set `total_is_authoritative=False`. Add a 304 test that reuses prior inventory and a force-full test that sends no ETag. Import `gem` from `src/fetchers/__init__.py`.

Fetch Gem detail from `https://api.gem.com/job_board/v0/{slug}/job_posts/{job_id}/`, require a JSON object with the matching `id`, and map its full content and provenance. SmartRecruiters uses the fixed-host detail URL above and treats a conflicting list `ref` as schema failure. Both adapters map only 404/410 to `CLOSED`; transient errors, 403, bad JSON, and schema mismatch are `FAILED` and remain scheduled for detail retry.

- [ ] **Step 5: Run tests and commit**

```bash
python -m pytest tests/test_fetchers_smartrecruiters_gem.py -v
git add src/fetchers/smartrecruiters.py src/fetchers/gem.py src/fetchers/__init__.py tests/fixtures tests/test_fetchers_smartrecruiters_gem.py
git commit -m "feat: add Intuitive and Chef job feeds"
```

Expected: all SmartRecruiters and Gem tests pass.

---

### Task 3: Complete Workday and provisional Rippling adapters

**Files:**
- Modify: `src/fetchers/workday.py:1-94`
- Create: `src/fetchers/rippling.py`
- Modify: `src/fetchers/__init__.py`
- Create: `tests/fixtures/workday_list_page_1.json`
- Create: `tests/fixtures/workday_list_page_2.json`
- Create: `tests/fixtures/workday_detail.json`
- Create: `tests/fixtures/rippling_jobs.json`
- Create: `tests/fixtures/rippling_jobs_repeated_location.json`
- Create: `tests/fixtures/rippling_detail.json`
- Create: `tests/test_fetchers_tier_b.py`

**Interfaces:**
- Consumes: shared fetch and detail contracts
- Produces: complete, detail-capable Workday and Rippling jobs

- [ ] **Step 1: Write failing pagination and detail tests**

Define a fresh module-local `fake_http(request)` fixture with a test-name-to-response mapping for the named Workday or Rippling list/detail fixtures. Create tests that prove:

Write these exact sanitized fixtures. `workday_list_page_1.json` is:

```json
{"total": 2, "jobPostings": [{"title": "Robotics Test Engineer I", "externalPath": "/job/Waltham-MA/Robotics-Test-Engineer-I_R12345", "locationsText": "Waltham, MA", "postedOn": "Posted 2 Days Ago"}]}
```

`workday_list_page_2.json` is distinct:

```json
{"total": 2, "jobPostings": [{"title": "Embedded Systems Engineer I", "externalPath": "/job/Waltham-MA/Embedded-Systems-Engineer-I_R67890", "locationsText": "Waltham, MA", "postedOn": "Posted 3 Days Ago"}]}
```

`workday_detail.json` is:

```json
{"jobPostingInfo": {"title": "Robotics Test Engineer I", "startDate": "2026-09-04", "posted": true, "canApply": true, "timeType": "Full time", "jobReqId": "R12345", "jobDescription": "Full-time robotics validation role. Sponsorship may be considered.", "externalUrl": "https://bostondynamics.wd1.myworkdayjobs.com/en-US/Boston_Dynamics/job/Waltham-MA/Robotics-Test-Engineer-I_R12345", "location": "Waltham, MA", "additionalLocations": [], "jobRequisitionLocation": {"country": {"alpha2Code": "US"}}}}
```

`rippling_jobs.json` is:

```json
[{"uuid": "rip-1", "name": "Robotics Hardware Engineer", "department": {"id": "hardware", "label": "Hardware"}, "url": "https://ats.rippling.com/foundation-robotics/jobs/rip-1", "workLocation": {"id": "sf", "label": "San Francisco, CA"}}]
```

`rippling_jobs_repeated_location.json` contains exactly two rows with the same `uuid`, name, department, and URL; the first has `workLocation: {"id": "sf", "label": "San Francisco, CA"}` and the second has `workLocation: {"id": "sj", "label": "San Jose, CA"}`. `rippling_detail.json` is:

```json
{"uuid": "rip-1", "createdOn": "2026-09-03T12:00:00Z", "employmentType": {"id": "Full time", "label": "SALARIED_FT"}, "workLocations": [{"id": "sf", "label": "San Francisco, CA"}], "description": {"company": "Foundation Robotics", "role": "Build and validate full-time robotic hardware."}, "payRangeDetails": null, "unlistedFromSearch": false, "board": {"slug": "foundation-robotics"}}
```

```python
def test_workday_uses_total_and_detail_start_date(fake_http, fetch_context, monkeypatch):
    monkeypatch.setattr(WorkdayFetcher, "PAGE_SIZE", 1)
    company = {
        "name": "Boston Dynamics",
        "fetcher": "workday",
        "host": "bostondynamics.wd1.myworkdayjobs.com",
        "tenant": "bostondynamics",
        "site": "Boston_Dynamics",
    }
    fetcher = WorkdayFetcher(fake_http)
    result = fetcher.fetch(company, fetch_context)
    detail = fetcher.fetch_detail(company, result.jobs[0])
    enriched = detail.job
    assert detail.status is DetailStatus.HEALTHY
    assert result.complete is True
    assert result.source_total == 2
    assert result.total_is_authoritative is True
    assert result.pages_fetched == 2
    assert enriched.posted_at == "2026-09-04"
    assert enriched.employment_type is EmploymentType.FULL_TIME
    assert "sponsorship" in enriched.description.lower()


def test_rippling_requires_expected_list_and_detail_shapes(fake_http, fetch_context):
    company = {"name": "Foundation Robotics", "fetcher": "rippling", "board": "foundation-robotics"}
    fetcher = RipplingFetcher(fake_http)
    result = fetcher.fetch(company, fetch_context)
    detail = fetcher.fetch_detail(company, result.jobs[0])
    enriched = detail.job
    assert detail.status is DetailStatus.HEALTHY
    assert enriched.posted_at == "2026-09-03T12:00:00Z"
    assert enriched.employment_type is EmploymentType.FULL_TIME
    assert enriched.salary is None
```

The repeated-location fixture contains two list rows with the same `uuid` and different `workLocation` values. Assert one normalized posting, aggregated deterministic locations, one active ID, `source_total == 1`, and `total_is_authoritative is False`. Freeze the captured Foundation oddity where `employmentType.label == "SALARIED_FT"` carries the machine code; map it to full-time and retain both `id` and `label` as raw metadata.

Use `monkeypatch` to set the Workday adapter page-size constant to 1 in its pagination test, with two distinct one-record list fixtures. Add a page-2 failure and assert partial with previous active IDs. Add missing/changing total, premature short or empty page, duplicate ID, repeated-page, and final-count mismatch cases; each is partial or failed and cannot close inventory. Assert every reconciled Workday total sets `total_is_authoritative=True`. Feed Rippling a top-level object instead of the verified top-level list and assert failed health, not empty-valid. Add healthy, 404, 410, transient, detail-ID mismatch, and schema-failure detail tests for both adapters.

- [ ] **Step 2: Run tests and verify existing completeness bugs**

```bash
python -m pytest tests/test_fetchers_tier_b.py -v
```

Expected: tests fail because Workday swallows later-page failures and lacks detail enrichment, while Rippling is absent.

- [ ] **Step 3: Implement complete Workday enumeration**

Workday uses this exact complete-board request and advances until `offset >= total`:

```python
list_url = f"https://{company['host']}/wday/cxs/{company['tenant']}/{company['site']}/jobs"
payload = {
    "appliedFacets": {},
    "limit": self.PAGE_SIZE,
    "offset": offset,
    "searchText": "",
}
page = self.http.post_json(list_url, payload=payload).data
total = page["total"]
rows = page["jobPostings"]
```

An absent/changing total, duplicate stable ID, repeated page, short or empty page before total, or `len(unique_ids) != total` at the boundary is incomplete. A first page with `total == 0` and `jobPostings == []` is `EMPTY_VALID`, authoritative, and complete; any other zero-row combination is not. Each sanitized list row contains exact keys `title`, `externalPath`, `locationsText`, and `postedOn`. Require a nonempty `title`. Require `externalPath` to end in an opaque requisition token such as `_R12345`; normalize that suffix as the stable list-time `job_id` and retain the full path in metadata. Map `locationsText` to the list-time display location, preserve `postedOn` only in metadata because it is relative display text rather than an authoritative date, and construct the public application URL as `https://{host}/en-US/{site}{externalPath}`. A path without a validated opaque suffix makes the snapshot incomplete rather than using the title-bearing full path as identity. Its detail URL is:

```python
detail_url = f"https://{company['host']}/wday/cxs/{company['tenant']}/{company['site']}{job.metadata['external_path']}"
```

Require a top-level object containing `jobPostingInfo`. Map `jobPostingInfo.startDate`, `posted`, `canApply`, `timeType`, `jobReqId`, `jobDescription`, `externalUrl`, primary/additional locations, and `jobRequisitionLocation.country.alpha2Code`. A matching HTTP 200 with `canApply: false` or an inactive-looking `posted` value is `FAILED` with a sanitized `official detail withheld application` warning; it is not proof of closure. Only an official 404 or 410 is `CLOSED`. Store `jobReqId` as the trusted employer `requisition_id` with official-detail provenance while retaining the path-derived source ID, and require it to match the opaque list suffix. Require the returned external path/URL identity to agree with the requested candidate. Map base compensation only when the response exposes an explicitly structured amount, currency, and period. Otherwise preserve any official salary prose in description/provenance for the later matching parser; the adapter must not depend on matching code that is implemented in a later plan. Never catch a later-page error and return accumulated jobs as complete. Include host, tenant, and site in the source key so two Workday sites cannot collide.

- [ ] **Step 4: Implement schema-guarded Rippling list and detail calls**

Use:

```python
base = f"https://api.rippling.com/platform/api/ats/v1/board/{company['board']}/jobs"
list_payload = self.http.get_json(base).data
detail_payload = self.http.get_json(f"{base}/{job.job_id}").data
```

Require the verified top-level JSON list for the roster. Deduplicate by `uuid`, aggregate every repeated `workLocation.id`/`label` into sorted unique locations, and count unique UUIDs in `source_total` and `active_ids`. A nonempty list uses that inferred unique count with `total_is_authoritative=False`; an explicit empty top-level list returns `EMPTY_VALID`, `source_total=0`, and `total_is_authoritative=True`. Map list fields `uuid`, `name`, `department.id`, `department.label`, `url`, and the aggregated locations. The exact captured detail fixture contains `uuid`, `createdOn`, `employmentType.id`, `employmentType.label`, `workLocations`, `description.company`, `description.role`, `payRangeDetails`, `unlistedFromSearch`, and `board.slug`. Require detail `uuid == job.job_id` and `board.slug == company['board']`; any mismatch fails. Interpret only `employmentType.label == "SALARIED_FT"` as full-time. An HTTP 200 detail with `unlistedFromSearch: true` is `FAILED` with a sanitized `official detail not publicly listed` warning; it is not closure evidence. Only an official 404 or 410 is `CLOSED`. Treat every other unrecognized shape as failed health because Rippling is provisional.

- [ ] **Step 5: Run tests and commit**

```bash
python -m pytest tests/test_fetchers_tier_b.py -v
git add src/fetchers/workday.py src/fetchers/rippling.py src/fetchers/__init__.py tests/fixtures tests/test_fetchers_tier_b.py
git commit -m "feat: complete tier b job feeds"
```

Expected: all Tier-B fixture tests pass.

---

### Task 4: Repair Amazon and implement fail-closed Tesla monitoring

**Files:**
- Create: `src/fetchers/amazon.py`
- Create: `src/fetchers/tesla.py`
- Modify: `src/fetchers/__init__.py`
- Modify: `src/fetchers/custom.py`
- Create: `tests/fixtures/amazon_page.json`
- Create: `tests/fixtures/amazon_detail.html`
- Modify: `tests/fakes.py`
- Create: `tests/test_fetchers_custom_official.py`

**Interfaces:**
- Consumes: shared fetch result and circuit behavior
- Produces: paginated U.S.-area `AmazonFetcher` with official detail-page verification and best-effort, schema-guarded `TeslaFetcher`

- [ ] **Step 1: Write failing Amazon request and pagination tests**

Define a module-local `fake_http(request)` fixture whose test-name mapping creates a fresh `FakeHttp` queue containing the named Amazon responses. The request-parameter and normalization tests receive `amazon_page.json`; `test_amazon_detail_uses_an_injected_clock` receives `amazon_page.json` followed by `amazon_detail.html`; error-path tests construct independent queues.

Write `amazon_page.json` with this exact sanitized body:

```json
{
  "hits": 2,
  "jobs": [
    {"id_icims": "us-job", "title": "Robotics Hardware Test Engineer", "job_path": "/en/jobs/us-job/robotics-hardware-test-engineer", "posted_date": "September 5, 2026", "updated_time": "2026-09-06T12:00:00Z", "description": "Validate robotic hardware.", "basic_qualifications": "2 years of test experience.", "preferred_qualifications": "Python experience.", "location": "Sunnyvale, California, USA", "normalized_location": {"city": "Sunnyvale", "region": "CA", "country_code": "USA"}},
    {"id_icims": "lu-job", "title": "Robotics Systems Engineer", "job_path": "/en/jobs/lu-job/robotics-systems-engineer", "posted_date": "September 4, 2026", "updated_time": "2026-09-06T11:00:00Z", "description": "Integrate robotic systems.", "basic_qualifications": "2 years of systems experience.", "preferred_qualifications": "ROS experience.", "location": "Luxembourg, Luxembourg", "normalized_location": {"city": "Luxembourg", "region": "Luxembourg", "country_code": "LUX"}}
  ]
}
```

Write `amazon_detail.html` as a minimal complete HTML document containing exactly one `<script type="application/ld+json">` with this payload and an enabled `Apply now` link:

```json
{"@context": "https://schema.org", "@type": "JobPosting", "identifier": {"name": "Amazon", "value": "us-job"}, "title": "Robotics Hardware Test Engineer", "description": "Validate robotic hardware and automate tests.", "datePosted": "2026-09-05", "validThrough": "2026-10-05T23:59:59Z", "employmentType": "FULL_TIME", "baseSalary": {"@type": "MonetaryAmount", "currency": "USD", "value": {"@type": "QuantitativeValue", "minValue": 110000, "maxValue": 165000, "unitText": "YEAR"}}, "jobLocation": {"@type": "Place", "address": {"@type": "PostalAddress", "addressLocality": "Sunnyvale", "addressRegion": "CA", "addressCountry": "US"}}}
```

The surrounding link is `<a href="/en/jobs/us-job/apply">Apply now</a>`. Error tests mutate this one fixture to an expired `validThrough`, add `aria-disabled="true"`, change the identifier, remove required fields, or return non-JSON-LD HTML. No test invents a successful Tesla payload.

```python
def test_amazon_uses_effective_us_area_parameters(fake_http, fetch_context):
    company = {"name": "Amazon Robotics", "fetcher": "amazon", "source_id": "robotics-us", "search_query": "robotics"}
    result = AmazonFetcher(fake_http).fetch(company, fetch_context)
    first_call = fake_http.calls[0]
    assert first_call.params["country"] == "USA"
    assert first_call.params["loc_query"] == "United States"
    assert first_call.params["type"] == "area"
    assert first_call.params["base_query"] == "robotics"
    assert first_call.params["latitude"] == "38.89037"
    assert first_call.params["longitude"] == "-77.03196"
    assert first_call.params["result_limit"] == 100
    assert first_call.params["offset"] == 0
    assert first_call.params["sort"] == "recent"
    assert "country[]" not in first_call.params
    assert result.complete is True


def test_amazon_normalizes_every_valid_row_so_inventory_and_jobs_agree(fake_http, fetch_context):
    result = AmazonFetcher(fake_http).fetch(
        {"name": "Amazon Robotics", "fetcher": "amazon", "source_id": "robotics-us", "search_query": "robotics"},
        fetch_context,
    )
    assert {job.country_code for job in result.jobs} == {"US", "LU"}
    assert result.source_total == 2
    assert result.total_is_authoritative is True
    assert result.active_ids == frozenset({"us-job", "lu-job"})
    assert {job.job_id for job in result.jobs} == result.active_ids


def test_amazon_detail_uses_an_injected_clock(fake_http, fetch_context):
    fixed_now = datetime(2026, 9, 7, 16, 0, tzinfo=timezone.utc)
    company = {"name": "Amazon Robotics", "fetcher": "amazon", "source_id": "robotics-us", "search_query": "robotics"}
    fetcher = AmazonFetcher(fake_http, now=lambda: fixed_now)
    job = fetcher.fetch(company, fetch_context).jobs[0]
    assert fetcher.fetch_detail(company, job).status is DetailStatus.HEALTHY
```

The mixed-location fixture contains one Sunnyvale role and one Luxembourg role with authoritative `hits: 2`; both become normalized Jobs and Stage 1 later rejects the foreign role. Add a separate synthetic pagination test that constructs 205 minimal U.S. records across 100, 100, and 5-record responses, then asserts request offsets `[0, 100, 200]`; this proves the old 100-result cap is gone. Add page-2 failure, missing/invalid/changing `hits`, duplicate ID, and repeated-page cases. Add a premature-short-page case whose authoritative total says 205 but page one returns 5; every case is partial or failed and preserves prior inventory. A malformed or location-ambiguous row also makes the snapshot incomplete rather than silently skipping an active ID. Add Amazon detail tests for an active JSON-LD job page, official 404, official 410, transient/403 failure, malformed HTML, and mismatched job ID.

- [ ] **Step 2: Write Tesla failure-contract tests**

```python
@pytest.mark.parametrize("status", [403, 429, 500])
def test_tesla_protected_responses_fail_closed(fetch_context, status):
    error = requests.HTTPError("official Tesla endpoint unavailable")
    error.response = Mock(status_code=status)
    http = FakeHttp([error])
    result = TeslaFetcher(http).fetch(
        {"name": "Tesla", "fetcher": "tesla", "board": "careers", "best_effort": True},
        fetch_context,
    )
    assert result.complete is False
    assert result.health == FetchHealth.FAILED
    assert result.active_ids == fetch_context.previous_active_ids
    assert result.jobs == ()
    assert result.error == f"Tesla official endpoint unavailable ({status})"


def test_tesla_unknown_200_schema_fails_closed(fetch_context):
    fake_http = FakeHttp([{"unexpected": []}])
    result = TeslaFetcher(fake_http).fetch(
        {"name": "Tesla", "fetcher": "tesla", "board": "careers", "best_effort": True},
        fetch_context,
    )
    assert result.health == FetchHealth.FAILED
    assert result.error == "Tesla official endpoint schema mismatch"
```

Add empty body, HTML, malformed JSON, and missing-job-collection cases. Assert the exact stable schema error, `jobs == ()`, `complete is False`, prior IDs preserved, and no closure transition. Do not invent a successful Tesla fixture. Until an official successful payload is captured and reviewed in a later change, every parser outcome is an explicit failed result that preserves state.

- [ ] **Step 3: Run tests and verify failures**

```bash
python -m pytest tests/test_fetchers_custom_official.py -v
```

Expected: current Amazon parameters and cap fail; `TeslaFetcher` is absent.

- [ ] **Step 4: Implement Amazon pagination and strict row validation**

Use these exact request parameters on `https://www.amazon.jobs/en/search.json`:

```python
params = {
    "base_query": company.get("search_query", "robotics"),
    "country": "USA",
    "loc_query": "United States",
    "latitude": "38.89037",
    "longitude": "-77.03196",
    "type": "area",
    "result_limit": 100,
    "offset": offset,
    "sort": "recent",
}
```

Require a stable nonnegative integer `hits` total and mark it authoritative. Continue until the offset reaches it and require `len(unique_ids) == hits`; a short page before that boundary or a final count mismatch is partial. A first response with `hits == 0` and `jobs == []` is `EMPTY_VALID`; any other zero-row combination is not. For each captured result row, use nonempty string `id_icims` as the stable ID, `job_path` joined to `https://www.amazon.jobs` as the official URL, `title` as the title, `posted_date` as the source posting date, `updated_time` as the source update timestamp, `description` plus `basic_qualifications` and `preferred_qualifications` as the list-time body, and `normalized_location` before the display `location` as the structured-location source. The exact U.S. fixture uses `normalized_location: {"city": "Sunnyvale", "region": "CA", "country_code": "USA"}` and the foreign fixture uses `{"city": "Luxembourg", "region": "Luxembourg", "country_code": "LUX"}`; normalize those codes to `US` and `LU`. If the captured payload supplies a documented alias, freeze that alias in its own fixture and test its priority; do not guess among arbitrary keys. Normalize every structurally valid row, including leaked foreign rows, so `active_ids == {job.job_id for job in jobs}` and lifecycle can observe a role moving out of the United States. Any malformed/ambiguous row, duplicate ID, repeated page, or total drift makes the whole snapshot incomplete.

Move only `AmazonFetcher` into `amazon.py` in this task. Keep `custom.py` and its other legacy adapters importable until Task 5 changes configuration, so intermediate commits and the CLI remain valid. Add `load_text_fixture()` beside `load_json_fixture()` and queue `TextResponse` through `FakeHttp.queue_text()` for the HTML tests. Give `AmazonFetcher` the exact constructor `__init__(self, http: HttpClient | None = None, *, now: Callable[[], datetime] = lambda: datetime.now(timezone.utc))`, call `super().__init__(http)`, store the callable as `self._now`, and compare `validThrough` with `self._now()`. This keeps registry construction backward compatible and fixes every detail test at `datetime(2026, 9, 7, 16, 0, tzinfo=timezone.utc)`. Implement `AmazonFetcher.fetch_detail()` with `HttpClient.get_text(job.url)`. Freeze a JSON-LD `JobPosting` fixture whose `@type` is exactly `JobPosting`, `identifier.value` equals the captured `id_icims`, `title` and `description` are nonempty, `datePosted` is parseable, and `validThrough` is either absent or not earlier than the injected current time. Treat a successful page with an expired `validThrough`, an explicit closed marker, or a disabled application control as `FAILED` with a sanitized warning because HTTP 200 is not sufficient closure proof. Require the identifier to match, then map description, date, employment type, base salary, structured location, and `OFFICIAL_DETAIL` provenance. Map only 404/410 to `CLOSED`; every other access, inactive-page signal, or schema problem is `FAILED` and leaves lifecycle state unchanged.

- [ ] **Step 5: Implement Tesla's best-effort failure boundary**

Call only `https://www.tesla.com/cua-api/apps/careers/state`. Map 403 and exhausted transient responses to a failed `FetchResult` with a sanitized error and `context.previous_active_ids`. Reject HTML, missing job collections, and unrecognized JSON. Do not infer closures. Keep parsing isolated in `parse_tesla_payload(payload)` so a future verified fixture can add success behavior without changing circuit or state code. Until a reviewed successful schema and explicit official detail verifier exist, the adapter emits no candidate jobs; it can never inherit the base detail method or pass Stage 2.

- [ ] **Step 6: Run tests and commit**

```bash
python -m pytest tests/test_fetchers_custom_official.py -v
git add src/fetchers/amazon.py src/fetchers/tesla.py src/fetchers/custom.py src/fetchers/__init__.py tests/fakes.py tests/fixtures tests/test_fetchers_custom_official.py
git commit -m "feat: repair Amazon and guard Tesla monitoring"
```

Expected: Amazon fixture tests pass and Tesla safely exposes protected access as a source failure.

---

### Task 5: Replace stale company configuration and verification output

**Files:**
- Modify: `config.yaml:1-355`
- Modify: `src/verify.py:1-75`
- Modify: `src/config.py`
- Modify: `src/health.py`
- Modify: `src/state.py`
- Modify: `src/source_health.py`
- Modify: `src/fetchers/base.py`
- Modify: `src/fetchers/__init__.py`
- Delete: `src/fetchers/custom.py`
- Create: `tests/fixtures/source_roster.json`
- Create: `tests/test_config.py`
- Create: `tests/test_adapter_normalization.py`
- Create: `tests/test_verify.py`
- Modify: `tests/test_health.py`
- Modify: `tests/test_source_lifecycle.py`

**Interfaces:**
- Consumes: registered adapter names and exact source keys from Tasks 1 through 4
- Produces: typed `AppSettings` and policy dataclasses, `load_config(path)`, `validate_config(settings)`, the exact approved 42-source roster, and a validation CLI that distinguishes healthy, empty-valid, partial, and failed

- [ ] **Step 1: Write failing configuration tests**

Assert no duplicate source keys, every enabled fetcher is registered, disabled companies are not counted as monitored, and these critical mappings are exact:

Build `tests/fixtures/source_roster.json` from this exact ordered tuple list. The tuple fields are `(name, fetcher, identifier_fields, source_key, status, priority, required_for_validation)`; serialize each as one object with the identifier fields expanded. This is the sole expected 42-row enabled roster:

```python
ROSTER_ROWS = [
    ("Figure", "greenhouse", {"slug": "figureai"}, "greenhouse:figureai", "validated", "high", True),
    ("Apptronik", "greenhouse", {"slug": "apptronik"}, "greenhouse:apptronik", "validated", "high", True),
    ("Nimble", "greenhouse", {"slug": "nimblerobotics"}, "greenhouse:nimblerobotics", "validated", "high", True),
    ("Neuralink", "greenhouse", {"slug": "neuralink"}, "greenhouse:neuralink", "validated", "high", True),
    ("Kodiak", "greenhouse", {"slug": "kodiak"}, "greenhouse:kodiak", "validated", "normal", True),
    ("Agility Robotics", "greenhouse", {"slug": "agilityrobotics"}, "greenhouse:agilityrobotics", "validated", "normal", True),
    ("Waymo", "greenhouse", {"slug": "waymo"}, "greenhouse:waymo", "validated", "normal", True),
    ("Formlabs", "greenhouse", {"slug": "formlabs"}, "greenhouse:formlabs", "validated", "normal", True),
    ("Torc Robotics", "greenhouse", {"slug": "torcrobotics"}, "greenhouse:torcrobotics", "validated", "normal", True),
    ("May Mobility", "greenhouse", {"slug": "maymobility"}, "greenhouse:maymobility", "validated", "normal", True),
    ("Nuro", "greenhouse", {"slug": "nuro"}, "greenhouse:nuro", "validated", "normal", True),
    ("Zipline", "greenhouse", {"slug": "flyzipline"}, "greenhouse:flyzipline", "validated", "normal", True),
    ("Diligent Robotics", "greenhouse", {"slug": "diligentrobotics"}, "greenhouse:diligentrobotics", "validated", "normal", True),
    ("Viam", "greenhouse", {"slug": "viamrobotics"}, "greenhouse:viamrobotics", "validated", "normal", True),
    ("Path Robotics", "greenhouse", {"slug": "pathrobotics"}, "greenhouse:pathrobotics", "validated", "normal", True),
    ("Carbon Robotics", "greenhouse", {"slug": "carbonrobotics"}, "greenhouse:carbonrobotics", "validated", "normal", True),
    ("Applied Intuition", "ashby", {"slug": "applied"}, "ashby:applied", "validated", "high", True),
    ("1X", "ashby", {"slug": "1x"}, "ashby:1x", "validated", "high", True),
    ("Matic", "ashby", {"slug": "Maticrobots"}, "ashby:Maticrobots", "validated", "normal", True),
    ("Fab2", "ashby", {"slug": "Fab2"}, "ashby:Fab2", "validated", "normal", True),
    ("Persona AI", "ashby", {"slug": "persona.ai"}, "ashby:persona.ai", "validated", "high", True),
    ("Skydio", "ashby", {"slug": "skydio"}, "ashby:skydio", "validated", "normal", True),
    ("Aurora", "ashby", {"slug": "aurora-operations-inc"}, "ashby:aurora-operations-inc", "validated", "normal", True),
    ("Standard Bots", "ashby", {"slug": "standardbots"}, "ashby:standardbots", "validated", "normal", True),
    ("Cobot", "ashby", {"slug": "cobot"}, "ashby:cobot", "validated", "normal", True),
    ("Gecko Robotics", "ashby", {"slug": "gecko-robotics"}, "ashby:gecko-robotics", "validated", "normal", True),
    ("Bedrock Robotics", "ashby", {"slug": "bedrock-robotics"}, "ashby:bedrock-robotics", "validated", "normal", True),
    ("Physical Intelligence", "ashby", {"slug": "physicalintelligence"}, "ashby:physicalintelligence", "validated", "normal", True),
    ("Serve Robotics", "ashby", {"slug": "serverobotics"}, "ashby:serverobotics", "validated", "normal", True),
    ("Generalist", "ashby", {"slug": "generalist"}, "ashby:generalist", "validated", "normal", True),
    ("Zoox", "lever", {"slug": "zoox"}, "lever:zoox", "validated", "high", True),
    ("Shield AI", "lever", {"slug": "shieldai"}, "lever:shieldai", "validated", "normal", True),
    ("Pickle Robot", "lever", {"slug": "picklerobot"}, "lever:picklerobot", "validated", "normal", True),
    ("Robust AI", "lever", {"slug": "robust-ai"}, "lever:robust-ai", "validated", "normal", True),
    ("Field AI", "lever", {"slug": "field-ai"}, "lever:field-ai", "validated", "normal", True),
    ("Dexterity", "lever", {"slug": "dexterity"}, "lever:dexterity", "validated", "normal", True),
    ("Intuitive", "smartrecruiters", {"slug": "Intuitive"}, "smartrecruiters:Intuitive", "validated", "high", True),
    ("Chef Robotics", "gem", {"slug": "chef-robotics"}, "gem:chef-robotics", "validated", "normal", True),
    ("Boston Dynamics", "workday", {"host": "bostondynamics.wd1.myworkdayjobs.com", "tenant": "bostondynamics", "site": "Boston_Dynamics"}, "workday:bostondynamics.wd1.myworkdayjobs.com:bostondynamics:Boston_Dynamics", "validated", "high", True),
    ("Foundation Robotics", "rippling", {"board": "foundation-robotics"}, "rippling:foundation-robotics", "provisional", "normal", False),
    ("Amazon Robotics", "amazon", {"source_id": "robotics-us", "search_query": "robotics"}, "amazon:robotics-us", "validated", "high", True),
    ("Tesla", "tesla", {"board": "careers", "best_effort": True}, "tesla:careers", "best-effort", "high", False),
]

EXPECTED_ROSTER = [
    {
        "name": name,
        "fetcher": fetcher,
        **identifiers,
        "source_key": key,
        "status": status,
        "priority": priority,
        "required_for_validation": required,
        "enabled": True,
    }
    for name, fetcher, identifiers, key, status, priority, required in ROSTER_ROWS
]
```

The one disabled descriptor is exactly `{"name": "Apple", "fetcher": "unavailable", "enabled": false, "reason": "official endpoint not validated"}` and is not part of `ROSTER_ROWS`. Update `src/fetchers/base.py` so `source_key(company)` uses `fetcher:slug` for Greenhouse/Ashby/Lever/SmartRecruiters/Gem, `rippling:board`, `amazon:source_id`, `tesla:board`, and the four-component Workday key shown above, rejecting a missing identity key instead of falling back to display name. Every adapter assigns that complete return value to `Job.source_key`. The test asserts equality to every stored `source_key`, not just uniqueness or count.

```python
EXPECTED = {
    "Figure": ("greenhouse", "figureai"),
    "Applied Intuition": ("ashby", "applied"),
    "Zoox": ("lever", "zoox"),
    "Skydio": ("ashby", "skydio"),
    "Shield AI": ("lever", "shieldai"),
    "Zipline": ("greenhouse", "flyzipline"),
    "Aurora": ("ashby", "aurora-operations-inc"),
    "Intuitive": ("smartrecruiters", "Intuitive"),
    "Chef Robotics": ("gem", "chef-robotics"),
    "Foundation Robotics": ("rippling", "foundation-robotics"),
    "Dexterity": ("lever", "dexterity"),
}
```

Add explicit assertions for Boston Dynamics host `bostondynamics.wd1.myworkdayjobs.com`, tenant `bostondynamics`, site `Boston_Dynamics`; Apple disabled with `fetcher: unavailable`; Amazon enabled with `search_query: robotics`; Tesla enabled with `best_effort: true`.

Create `tests/fixtures/source_roster.json` as the exact ordered 42-entry enabled roster from the approved design. Each expected row contains company, fetcher, adapter identifier fields, `status` (`validated`, `provisional`, or `best-effort`), `priority`, and `required_for_validation`. Assert exact list equality, not a subset: 16 Greenhouse, 14 Ashby, 6 Lever, one SmartRecruiters, one Gem, one Workday, one Rippling, Amazon, and Tesla. Assert Apple is the only disabled row and is excluded from the 42 monitored sources. This catches accidental additions such as Oracle or old defense-heavy entries.

Add `tests/test_adapter_normalization.py` after every adapter exists. Parameterize Ashby `FullTime`, SmartRecruiters `FULL_TIME`, Workday `Full time`, and Rippling `SALARIED_FT`; assert all normalize to `EmploymentType.FULL_TIME`. Cover onsite/hybrid/remote values and `1 YEAR`/`HOUR` into `WorkplaceType` and `PayPeriod`. Preserve raw values only in metadata, and leave missing currency empty.

For each representative adapter record, also assert normalized `city`, `region`, `country_code`, official application URL, and per-field provenance. Cover the exact captured Ashby primary location, Lever country/salary/apply URL, SmartRecruiters location/application URL, Workday detail country, Rippling aggregated locations, Amazon foreign-row preservation, and official-detail description provenance. Do not invent an Ashby `secondaryLocations` shape: add that coverage only with a sanitized captured official field shape. Assert `source_key(company) == expected_row["source_key"]` for all 42 configured sources. For representative fixture Jobs from every successful adapter, assert `job.source_type == company["fetcher"]` and `job.source_key == source_key(company)`. Tesla is explicitly excluded from Job assertions because its quarantined adapter has no verified success schema and emits no Jobs. These are contract tests for the matching gate, not merely enum tests.

- [ ] **Step 2: Run tests and verify stale mappings fail**

```bash
python -m pytest tests/test_config.py tests/test_adapter_normalization.py tests/test_verify.py -v
```

Expected: failures identify the current Boston Dynamics, Intuitive, Zoox, Skydio, Shield AI, Zipline, Applied Intuition, Aurora, Figure, Dexterity, Apple, and Tesla entries.

- [ ] **Step 3: Replace `config.yaml` with the approved source roster**

Start the file with these exact policies:

```yaml
fetch_policy:
  max_attempts: 3
  connect_timeout_seconds: 10
  read_timeout_seconds: 30
  backoff_base_seconds: 2
  backoff_cap_seconds: 60
  per_host_pacing_seconds: 0.25
  circuit_failure_threshold: 3
  half_open_probe_hours: 24
  shrink_ratio: 0.40
  shrink_min_previous_count: 20
state_policy:
  closed_candidate_days: 90
  delivered_days: 365
  delivered_limit: 10000
  pending_max_age_days: 30
  digest_entry_days: 30
  health_event_limit: 30
matching_policy:
  freshness_days: 30
  same_id_reopen_days: 7
  compensation_material_change_ratio: 0.10
  compensation_threshold: 100000
digest:
  timezone: America/New_York
  send_after: "19:30"
  persistent_health_warning_runs: 2
telegram:
  max_attempts: 3
  backoff_base_seconds: 2
  backoff_cap_seconds: 60
  minimum_send_interval_seconds: 1
  message_limit: 3900
```

Enable the validated Greenhouse slugs `figureai`, `apptronik`, `nimblerobotics`, `neuralink`, `kodiak`, `agilityrobotics`, `waymo`, `formlabs`, `torcrobotics`, `maymobility`, `nuro`, `flyzipline`, `diligentrobotics`, `viamrobotics`, `pathrobotics`, and `carbonrobotics`.

Enable the validated Ashby slugs `applied`, `1x`, `Maticrobots`, `Fab2`, `persona.ai`, `skydio`, `aurora-operations-inc`, `standardbots`, `cobot`, `gecko-robotics`, `bedrock-robotics`, `physicalintelligence`, `serverobotics`, and `generalist`.

Enable the validated Lever slugs `zoox`, `shieldai`, `picklerobot`, `robust-ai`, `field-ai`, and `dexterity`; SmartRecruiters `Intuitive`; Gem `chef-robotics`; Boston Dynamics Workday; provisional Foundation Rippling; Amazon Robotics; and best-effort Tesla. Give Tesla explicit adapter identifier `board: careers`, producing canonical source key `tesla:careers`. Every row carries an explicit `status`. Validated enabled sources default to `required_for_validation: true`; Foundation Robotics and Tesla are explicitly false because they are provisional and best-effort. Mark Tesla, Amazon Robotics, Figure, Apptronik, Nimble, Persona AI, 1X, Applied Intuition, Zoox, Boston Dynamics, Intuitive, and Neuralink as `priority: high`; every other enabled source is `priority: normal`. Keep Apple as the only disabled row with reason `official endpoint not validated`. Delete `custom.py` only after `src/fetchers/__init__.py` no longer imports it and an import/registry smoke test passes.

- [ ] **Step 4: Implement strict configuration validation and truthful verification**

Create frozen `FetchPolicy`, `StatePolicy`, `MatchingPolicy`, `DigestPolicy`, `TelegramPolicy`, `SourceConfig`, and `AppSettings` dataclasses. `load_config()` is the only YAML boundary and returns `AppSettings`; class defaults are fallbacks for omitted optional settings, while runtime constructors receive the loaded values explicitly. `SourceConfig.as_fetcher_mapping()` returns a fresh plain dictionary with name, fetcher, enabled flag, and adapter-specific identifiers, preserving the existing fetcher contract without exposing typed config for mutation. Update `src.health` and `src.verify` to accept `AppSettings` and pass only that fresh dictionary to `source_key()`/fetchers. `validate_config()` returns errors for unknown fetchers on enabled rows, duplicate names/source keys, roster drift, missing adapter-specific keys, invalid status/priority values, nonboolean `required_for_validation`, invalid circuit thresholds, and enabled provisional sources without `status: provisional`. Disabled rows are visible descriptors and need not register a runnable adapter.

Validate revision settings before constructing runtime policies. Require `same_id_reopen_days` to be a real integer rather than a Boolean and at least 1. Convert `compensation_material_change_ratio` and `compensation_threshold` through `Decimal(str(value))`; reject Boolean, nonnumeric, nonfinite, zero, or negative values, and reject a material-change ratio greater than 1. Tests cover the valid boundary values `same_id_reopen_days=1`, `compensation_material_change_ratio=1`, and a positive one-cent threshold, plus invalid zero, negative, Boolean, ratio-greater-than-one, `NaN`, and infinity cases for the applicable field.

Construct both core policies from the validated settings:

```python
health_policy = SourceHealthPolicy(
    failure_threshold=settings.fetch_policy.circuit_failure_threshold,
    probe_interval=timedelta(hours=settings.fetch_policy.half_open_probe_hours),
    shrink_ratio=Decimal(str(settings.fetch_policy.shrink_ratio)),
    shrink_min_previous_count=settings.fetch_policy.shrink_min_previous_count,
)
revision_policy = RevisionPolicy(
    same_id_reopen_days=settings.matching_policy.same_id_reopen_days,
    compensation_material_change_ratio=Decimal(
        str(settings.matching_policy.compensation_material_change_ratio)
    ),
    compensation_threshold=Decimal(
        str(settings.matching_policy.compensation_threshold)
    ),
)
state = StateManager.load(
    state_path,
    limits,
    health_policy=health_policy,
    revision_policy=revision_policy,
)
```

Use that exact two-policy injection in health, verification, and orchestration; no call site may silently fall back to `RevisionPolicy()` after configuration is loaded. Add a composition test proving loaded retry, timeout, pacing, shrink, circuit, revision, and retention settings reach `HttpClient`, `SourceHealthPolicy`, `RevisionPolicy`, `StateManager`, and `StateLimits`. Assert `state.health_policy is health_policy` and `state.revision_policy is revision_policy`, then exercise one nondefault reopen interval and both nondefault compensation values so the test proves behavior rather than only object construction. Delivery Task 6 extends the same composition test to Telegram, formatter, and digest.

`src.verify` constructs `FetchContext`, prints health, circuit-independent completeness, page count, source total, warnings, and sample normalized records. It returns `0` when every required source is healthy or explicit empty-valid, even if a nonrequired source is partial/failed/open; `1` when any required source is partial, failed, or open; and `2` only for configuration or state corruption. Every nonrequired failure, including Foundation Robotics and Tesla, remains a visible warning. It never prints `FETCH OK` for an unexplained empty or failed result.

- [ ] **Step 5: Run adapter and configuration tests**

```bash
python -m pytest tests/test_fetchers_tier_a.py tests/test_fetchers_smartrecruiters_gem.py tests/test_fetchers_tier_b.py tests/test_fetchers_custom_official.py tests/test_adapter_normalization.py tests/test_config.py tests/test_verify.py tests/test_health.py tests/test_source_lifecycle.py -v
```

Expected: all source and configuration tests pass without network access.

- [ ] **Step 6: Commit the validated roster**

```bash
git add config.yaml src/config.py src/health.py src/state.py src/source_health.py src/verify.py src/fetchers/base.py src/fetchers/__init__.py src/fetchers/custom.py tests/fixtures/source_roster.json tests/test_adapter_normalization.py tests/test_config.py tests/test_verify.py tests/test_health.py tests/test_source_lifecycle.py
git commit -m "config: enable verified robotics job sources"
```

## Source Adapter Verification Gate

Run:

```bash
python -m pytest -q
python -m compileall -q src tests
python -m src.verify --help
git status --short
```

Expected: the full suite passes and the worktree is clean. Live `validate-only` checks occur in the deployment plan, not here.
