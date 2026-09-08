# Job Tracker Alerts V2 Implementation Plan

**Status:** Approved for implementation on 2026-09-07.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Repair Muhammad's GitHub job tracker so it finds fresh, suitable U.S. engineering roles, recommends the correct tailored CV, and delivers reliable Telegram alerts without losing jobs.

**Architecture:** The approved design is split into four dependency-ordered implementation plans because source retrieval, candidate matching, transactional delivery, and deployment each require an independent review gate. The shared typed model and state contract is built first; every later plan consumes those exact interfaces and ends with the full test suite and a clean commit.

**Tech Stack:** Python 3.11, requests, PyYAML, pytest, GitHub Actions, Telegram Bot API

**Spec:** `docs/superpowers/specs/2026-09-07-job-tracker-alerts-v2-design.md`

## Global Constraints

- Work only on branch `codex/job-tracker-alerts-v2` until the internal-browser deployment review.
- Use test-driven development: add a focused failing test, confirm the expected failure, add minimal production code, confirm the test passes, then commit.
- Keep official company career pages and feeds as the source of record. Do not use search results as proof that a role is active.
- Never send a Telegram message during local development or automated tests. The first real test message requires explicit action-time confirmation.
- Never submit an application, contact a recruiter, upload a resume, or modify unrelated repository files.
- Preserve the user's existing Telegram secret values. Never print, log, store, or expose them.
- Treat Tesla as best-effort and fail closed until an official successful schema is verified.
- Maintain exact confirmed-versus-inferred labels and exact CV filenames from the approved design.

---

## File Structure

### Core and configuration

- `src/models.py`: immutable source, job, evaluation, and alert contracts
- `src/config.py`: strict runtime, source-roster, fetch, state, matching/lifecycle, alert, and digest configuration loading
- `src/profile.py`: strict immutable candidate, scoring, skill-alias, and resume-route profile loading
- `profile.yaml`: candidate evidence, scoring version, role families, and exact resume routes
- `config.yaml`: source roster plus fetch, state, matching/lifecycle, alert, and digest policies

### Fetching and source health

- `src/fetchers/http.py`: bounded retries, shared host pacing, ETags, timeouts, and sanitized failures
- `src/fetchers/base.py`: registry plus fetch/detail interfaces
- `src/fetchers/*.py`: one standard or company-specific adapter per responsibility
- `src/source_health.py`: snapshot validation, shrink detection, circuit transitions, and probe scheduling
- `src/lifecycle.py`: active inventory, closure, reopen, and material revision behavior
- `src/dedupe.py`: canonical URL/posting identity and probable duplicate evidence

### Matching

- `src/eligibility.py`: Stage-1 role, seniority, U.S. location, and employment gate
- `src/parsing.py`: official job-section extraction
- `src/experience.py`: required/preferred years and flexible-four-year logic
- `src/authorization.py`: sponsorship, OPT, citizenship, clearance, and export classification
- `src/compensation.py`: annual base-pay normalization and $100,000 labels
- `src/freshness.py`: authoritative 30-day and post-seed freshness
- `src/qualifications.py`: required qualification grouping and profile-evidence coverage
- `src/scoring.py`: version-1 100-point assessment and priority tier
- `src/resumes.py`: ordered exact CV routing
- `src/evaluation.py`: final verified assessment boundary

### Delivery and operation

- `src/alert_formatting.py`: provenance labels, safe HTML, truncation, and message chunks
- `src/alerts.py`: Telegram credential validation and transport
- `src/delivery.py`: persisted-queue-only, chunk-level at-least-once delivery
- `src/digest.py`: daily moderate and source-health summaries
- `src/state.py`: migration, compact persistence, retention, queues, and atomic writes
- `src/state_merge.py`: deterministic state-delta replay after remote updates
- `src/orchestrator.py`: concurrent fetch with single-threaded state mutation and run modes
- `src/reporting.py`: sanitized versioned phase reports and durable same-run metrics
- `src/main.py`: CLI only
- `src/health.py`: truthful source/circuit report
- `.github/workflows/check_jobs.yml`: tests, twice-hourly schedule, safe state synchronization, and deployment inputs
- `README.md`: user-facing operation, timing, limitations, and recovery
- `tests/`: offline fixtures and focused assertion-based coverage

---

### Task 1: Build and approve the core foundation

**Plan:** `docs/superpowers/plans/2026-09-07-job-tracker-core-foundation.md`

**Produces:** typed models, resilient HTTP, fetch context/result contracts, version-2 migration, atomic compact state, lifecycle safety, circuit recovery, deduplication, and truthful health output.

- [ ] Execute every task and verification gate in the core-foundation plan.
- [ ] Confirm the full available suite passes and the phase ends in clean, focused commits.

### Task 2: Replace and verify source adapters

**Plan:** `docs/superpowers/plans/2026-09-07-job-tracker-source-adapters.md`

**Consumes:** Task 1 model, HTTP, source context/result, state, and health contracts.

**Produces:** the exact 42-source approved roster, complete ATS pagination, detail enrichment, corrected Amazon U.S. search, visible disabled Apple source, and fail-closed Tesla monitoring.

- [ ] Execute every task and verification gate in the source-adapter plan.
- [ ] Confirm all adapter tests are offline and reject unmocked network access.

### Task 3: Implement eligibility, scoring, and CV routing

**Plan:** `docs/superpowers/plans/2026-09-07-job-tracker-matching.md`

**Consumes:** Task 1 models/state and Task 2 normalized listing/detail records.

**Produces:** structured U.S./full-time gate, experience and authorization evidence, 30-day freshness, salary labels, exact version-1 score, priorities, missing-qualification flags, and exact tailored-CV selection.

- [ ] Execute every task and verification gate in the matching plan.
- [ ] Confirm no role can alert with unresolved full-time status, a hard authorization block, stale authoritative date, required experience above the approved limit, or missing resume route.

### Task 4: Implement delivery, workflow, and controlled deployment

**Plan:** `docs/superpowers/plans/2026-09-07-job-tracker-delivery-deployment.md`

**Consumes:** every prior interface and assessment.

**Produces:** safe Telegram formatting, bounded transport, durable queue-before-send phases, stale-queue invalidation, per-chunk delivery receipts, daily digest, isolated smoke mode, explicitly confirmed persisted-queue recovery, deterministic state replay, default-off twice-hourly workflow, migration rehearsal, documentation, and controlled activation.

- [ ] Execute every task through offline tests, internal-browser branch review, validation mode, and seed mode.
- [ ] Stop immediately before the first real Telegram test message and obtain explicit action-time confirmation.
- [ ] After confirmation, send one test, observe one scheduled run, and verify the final gate.

## Cross-Plan Verification

After every phase, run:

```bash
python -m pytest -q
python -m compileall -q src tests
git status --short
```

Expected: all implemented tests pass, all modules compile, and the worktree is clean. Do not begin the next phase if any command fails.

Before deployment, additionally verify:

```bash
python -m src.main --mode dry-run --state state.json
git diff --exit-code -- state.json
```

Expected: dry-run may show preview matches but does not send Telegram, modify `state.json`, or consume future alerts.

## Commit Sequence

The task plans create these reviewable commits in order:

1. `refactor: define tracker domain models`
2. `feat: add resilient fetch contract`
3. `feat: add transactional state v2`
4. `feat: track source lifecycle safely`
5. `feat: report source health and circuit state`
6. `feat: repair primary ATS adapters`
7. `feat: add Intuitive and Chef job feeds`
8. `feat: complete tier b job feeds`
9. `feat: repair Amazon and guard Tesla monitoring`
10. `config: enable verified robotics job sources`
11. `refactor: add structured stage one eligibility`
12. `feat: parse job eligibility evidence`
13. `feat: add versioned job scoring`
14. `feat: route roles to tailored resumes`
15. `feat: evaluate verified job matches`
16. `feat: format provenance-aware job alerts`
17. `feat: make Telegram delivery bounded and explicit`
18. `feat: persist chunk-level delivery receipts`
19. `feat: add daily moderate and health digests`
20. `feat: add deterministic state deltas`
21. `refactor: orchestrate durable tracker modes`
22. `ci: schedule durable gated job scans`
23. `docs: explain reliable job alerts`

## Nonnegotiable Acceptance Matrix

Before controlled activation, the full suite must prove all of these together:

- every Stage-1 candidate is explicitly detail-verified by its adapter; the base class fails closed
- version-1 alerted identities suppress the first equivalent version-2 alert by official URL or posting alias
- the exact 42-source enabled roster and one disabled Apple descriptor match the approved fixture
- queue state reaches the default branch before Telegram delivery can begin, and failed queue sync prevents sending
- queue-scoped promotion, receipt, and delta tombstones cannot delete the promoted immediate copy or resurrect obsolete Moderate or delivered entries
- official closure, Stage-1 rejection, or a later ineligible assessment invalidates pending alerts before transport; delivery also rejects stale queue records defensively
- per-source seed markers exist for every required source before activation
- scheduled live work is blocked while `JOB_TRACKER_LIVE_ENABLED` is false
- isolated smoke-test mode sends exactly one approved payload without scanning or mutating state
- disabled-live recovery can send only one explicitly confirmed queued run and cannot rescan, digest, or broaden its target
- 95/100 same-run on-time deliveries pass, 94/100 fail, and a zero denominator is not applicable
- no-change runs emit a validated `has_changes=false` phase delta but create no dirty state or commit
- unresolved detail retries force a full listing refresh after a conditional 304 path
- source-scoped IDs, trusted requisitions, reposts, and reopen generations cannot collide
- an official Cobot U.S.-person requirement is blocked end to end
- one nonempty scheduled production run on the default branch meets the 95 percent delivery target after activation
