# Source adapters and runtime configuration report

## Implementation

- Added one shared HTTPS transport with bounded retries, connect and read timeouts, capped exponential backoff, `Retry-After` support, ETag handling, sanitized failures, and process-wide per-host request pacing.
- Added strict fetch and detail contracts for Greenhouse, Ashby, Lever, SmartRecruiters, Gem, Workday, Rippling, Amazon, and Tesla. Complete inventories are distinguished from valid empty inventories, partial pagination, and source failures.
- Added stable source identities for every adapter and a registry that exports `source_key` for orchestration and delivery callers.
- Added frozen runtime settings, strict YAML loading, exact policy composition helpers, and the approved 42-source enabled roster. Apple remains the only disabled descriptor because its official endpoint is not validated.
- Added deterministic health reporting and a read-only verifier. `--detail-sample` verifies one role-relevant official detail record for every nonempty source without saving state or sending alerts.
- Replaced the legacy custom fetcher module. Tesla intentionally emits no jobs and preserves previous inventory when its protected official endpoint cannot be verified.

## Red and green evidence

- HTTP and base contract red: imports and new method signatures were absent. Green: 14 focused tests passed.
- Tier A red: 16 failures and 3 passes exposed incomplete list and detail behavior. Green: 19 focused tests passed.
- SmartRecruiters and Gem red: the Gem adapter was absent. Green: 16 focused tests passed.
- Workday and Rippling red: the Rippling adapter was absent and pagination contracts were incomplete. The final focused suite passed 17 tests.
- Amazon and Tesla red: the Amazon adapter was absent under the new module and Tesla had no fail-closed implementation. The final focused suite passed 27 tests.
- Configuration, normalization, health, and verification were introduced from failing imports and contract assertions. The final owned suite passed 133 tests.
- Current Amazon live location alias red: `normalized_location` changed from an object to a display string with exact top-level `city`, `state`, and `country_code` fields. The frozen regression failed before the narrow parser update and passed afterward.
- Current Amazon semantic detail red: the official page no longer exposed the planned JSON-LD record. The frozen semantic HTML regression failed before support for the exact title, job ID, section, location, and apply-control structure and passed afterward.
- Current Workday pagination red: Boston Dynamics reports its authoritative total only on page one and returns `total: 0` with nonempty later pages. The three-page sentinel regression failed before the narrow compatibility rule and passed afterward.
- Final owned source, HTTP, configuration, health, and verification suite: 133 tests passed. At the source integration checkpoint, the repository suite passed 481 tests in 1.56 seconds. A later parallel matching review added its next failing tests after this checkpoint; those failures are outside the source-owned files and remain with the matching implementer.

## Live read-only validation

The final command was:

```text
.venv/bin/python -m src.verify --state /private/tmp/job-tracker-readonly-validation-20260907-state.json --show 0 --detail-sample
```

It exited 0. All 40 required sources returned healthy, complete inventories and a healthy sampled detail record. Foundation Robotics, a nonrequired provisional source, also returned a healthy complete inventory and detail. Boston Dynamics completed 4 pages with 79 unique jobs. Amazon Robotics completed 10 pages with 1000 unique jobs. Tesla remained visibly failed on the expected official 403, but it is nonrequired and fail-closed. Apple remained disabled. The temporary state path was not created, and no Telegram operation ran.

## Contracts and practical decisions

- `get_fetcher(name, http=None)` returns only a registered official adapter. Every adapter implements `fetch(company, context)` and an explicit `fetch_detail(company, job)`.
- `source_key(company)` uses adapter-specific immutable identities: slug; host, tenant, and site; board; or source ID.
- Only a complete, reconciled result can drive omission closure. Failures and partial pages retain `FetchContext.previous_active_ids`.
- A first-page authoritative total remains mandatory for paginated Workday. The only accepted later-page exception is exact `total == 0` with a nonempty page. Pagination continues against the first-page total and succeeds only when the final unique identity count matches it exactly.
- Amazon accepts two frozen official shapes: the planned JSON-LD job posting and the current semantic server-rendered detail page. Both require matching job identity, an enabled official apply control, nonempty required content, and unambiguous United States location evidence. Any other shape fails closed.
- Tesla calls only the official protected careers endpoint. A 403, transient exhaustion, HTML, empty malformed data, or unknown JSON can never become a healthy zero-job snapshot.
- `load_config(path)` is the only YAML boundary. It returns immutable `AppSettings` and rejects unknown keys, bad types, missing source identities, duplicate identities, policy values outside their allowed bounds, and roster drift.
- `build_retry_policy`, `build_state_limits`, `build_health_policy`, and `build_revision_policy` are the runtime composition boundary. Telegram settings expose `max_attempts=3`, `timeout_seconds=15`, `backoff_base_seconds=2`, `backoff_cap_seconds=60`, `minimum_send_interval_seconds=1`, and `message_limit=3900`.
- `run_verification` and `build_health_report` are read-only. Required-source failures affect exit status; nonrequired failures remain visible without blocking activation.
