# Job Tracker Alerts V2 Design

Date: 2026-09-07

## Purpose

Upgrade the existing GitHub Actions job tracker so it reliably discovers newly published, currently open United States engineering roles that match Muhammad Ahmed Nazir Shaikh's background and delivers useful alerts through Telegram.

The tracker should favor quality over volume. It should prioritize full-time roles requiring zero to three years of experience across robotics systems, integration, hardware validation, electromechanical test, mechatronics, embedded systems, sensing, manufacturing test, controls, automation, and mechanical product development. Published compensation of at least $100,000 is preferred but is not required when compensation is absent. Roles with confirmed citizenship, clearance, ITAR, or incompatible export-control requirements must not be recommended.

## Current Problems

The current system reports successful workflow runs while important parts of the pipeline are unhealthy:

- Six enabled sources are permanently skipped after six failures because the recovery path never probes them again.
- Apple swallows a 404 and returns an empty list, which the orchestrator records as a healthy fetch.
- Amazon's query uses software and data categories that omit many hardware, process, test, and manufacturing roles. It is capped at 100 results and does not reliably enforce the United States filter.
- Thirty-six of fifty-two configured companies are disabled. Tesla, Figure, Applied Intuition, Nimble, Persona AI, and other high-priority employers are not monitored correctly.
- Workday jobs lack descriptions, so experience and work-authorization requirements cannot be evaluated.
- Location matching uses substrings. For example, `usa` matches `Lausanne`, and global remote roles can be treated as United States roles.
- There is no actual zero-to-three-year experience parser or freshness check.
- Every newly discovered job is marked seen before Telegram succeeds. A failed send permanently loses the alert.
- Missing Telegram credentials are logged like a dry run and reported as a successful send.
- The current dry-run mode mutates and commits production state.
- `state.json` is 6.3 MB and contains about 18,669 records, while only about 243 were alerted. Repeated bot commits are creating unnecessary repository churn.
- The workflow requests a five-minute schedule, but recent runs have arrived hours apart. GitHub-hosted schedules are not real-time and can be delayed or dropped.
- The existing filter test prints results but has no assertions, cannot fail CI, and does not cover delivery or source-health behavior.

## Selected Approach

Keep the free GitHub Actions and Python architecture, repair it in place, and continue using the existing Telegram bot credentials. WhatsApp is intentionally excluded because proactive WhatsApp messages require more account setup, recipient opt-in, template approval, token management, and ongoing maintenance.

The design uses official company ATS feeds whenever possible, verifies candidate details before alerting, stores compact transactional state, and provides at-least-once Telegram delivery.

## Alert Timing

The scan workflow will request two runs per hour at minutes 17 and 47. Choosing off-peak minutes reduces contention compared with minute 0. Strong matches are sent immediately after a successful scan and validation.

A moderate-match digest is sent once per New York calendar day after 7:30 PM America/New_York time. The scan process checks a stored `last_digest_date`, so a delayed workflow sends the missed digest on the next successful run rather than skipping it.

For healthy twice-hourly sources, expected delivery is normally within 15 to 45 minutes of an official feed exposing a role. GitHub Actions can still delay a run by several hours, so this is a realistic target rather than a guarantee. Tesla is a best-effort exception because its public careers endpoint currently blocks some automated requests; its delivery time depends on source health and may be longer.

## Source Architecture

### Source result contract

Every fetcher returns a `FetchResult` instead of a bare job list. The result includes:

- normalized jobs
- whether the snapshot is complete
- source-reported total, when available
- number of pages fetched
- fetch timestamp
- ETag or response fingerprint, when available
- warnings and a sanitized error
- source-health status: healthy, empty-valid, partial, or failed
- circuit state, tracked separately: closed, open, or half-open

An empty result is healthy only when the source explicitly reports zero active jobs. A page failure makes the snapshot partial, not successful. Closure detection and source recovery use only complete snapshots.

### Source tiers

1. Structured official feeds: Greenhouse, Ashby, Lever, SmartRecruiters, and Gem. These are the primary sources and should enumerate all active public jobs.
2. Structured but less stable feeds: Workday CXS, Oracle Recruiting Cloud, and company career-board backends such as Rippling. These remain isolated behind adapters with schema and completeness checks.
3. Company-specific official sites: Amazon, Tesla, and other custom career systems. These run through dedicated adapters with strict health checks and final detail-page verification.

### Initial source roster

The repaired configuration will prioritize this roster. `Validated` means the endpoint, pagination, and representative detail data were checked live. `Provisional` means the endpoint works but is not a documented public contract. `Best-effort` means access is currently inconsistent and cannot carry the same freshness guarantee.

- Validated Greenhouse: Figure (`figureai`), Apptronik, Nimble (`nimblerobotics`), Neuralink, Kodiak, Agility Robotics, Waymo, Formlabs, Torc Robotics, May Mobility, Nuro, Zipline (`flyzipline`), Diligent Robotics, Viam, Path Robotics, and Carbon Robotics.
- Validated Ashby: Applied Intuition (`applied`), 1X (`1x`), Matic (`Maticrobots`), Fab2, Persona AI (`persona.ai`), Skydio, Aurora (`aurora-operations-inc`), Standard Bots, Cobot (`cobot`), Gecko Robotics (`gecko-robotics`), Bedrock Robotics (`bedrock-robotics`), Physical Intelligence (`physicalintelligence`), Serve Robotics (`serverobotics`), and Generalist (`generalist`). Cobot jobs still pass the same per-job U.S.-person and export-control gate.
- Validated Lever: Zoox (`zoox`), Shield AI (`shieldai`), Pickle Robot (`picklerobot`), Robust AI (`robust-ai`), Field AI (`field-ai`), and Dexterity (`dexterity`).
- Validated SmartRecruiters: Intuitive using its public company identifier and complete pagination.
- Validated Gem: Chef Robotics (`chef-robotics`).
- Validated Workday: Boston Dynamics using its verified tenant and site.
- Provisional Rippling: Foundation Robotics. Its public career-board endpoint is live but is not a documented contract.
- Validated custom Amazon adapter: Amazon Robotics using its current United States area parameters and per-record location validation.
- Best-effort custom Tesla adapter: use the official careers endpoint only while it is healthy, with conservative probing after access failures.

Zoox, Shield AI, Skydio, Aurora, Intuitive, Pickle Robot, Robust AI, and Zipline move from broken or missing configurations to their validated feeds. Apple remains disabled until a current official source can be validated. Every disabled source must be visible in health reporting and must never be counted as monitored.

### Fetch reliability

HTTP clients use at most three attempts for timeouts, 429, 500, 502, 503, and 504 responses. They honor `Retry-After`, otherwise use exponential backoff with a two-second base, jitter, and a 60-second cap. Defaults are a 10-second connection timeout, 30-second read timeout, and per-host pacing. Clients never log credentials or token-bearing URLs. Conditional requests reuse ETags where supported; a 304 response counts as a successful, unchanged, complete snapshot using the preceding source inventory.

A circuit breaker opens after three consecutive failed or incomplete fetches. An open source receives one half-open probe every 24 hours. A successful complete probe closes the circuit and restores the normal schedule. A source can no longer remain permanently skipped with no recovery attempt.

Amazon requests use the current United States area query shape rather than the ignored `country[]=USA` parameter, then validate every returned location independently. Tesla's current public careers-state endpoint can return 403 to automated clients. A 403, empty response, or schema change is a source failure, never evidence that jobs closed. The adapter keeps the last complete snapshot, retries conservatively, and falls back to a daily health probe while quarantined. Search-engine results are not used as the source of record.

A complete snapshot is treated as implausibly shrunken when it contains less than 40 percent of the preceding complete count and the preceding count was at least 20, unless an authoritative source total confirms the decrease. This threshold, the three-failure circuit threshold, the 24-hour probe interval, and retry limits live in configuration and are covered by tests.

## Job Model and Freshness

The normalized job record contains:

- stable identity: source type, board or tenant, and posting ID
- company, title, location, and official application URL
- full-time status and workplace type
- description and content hash
- source-posted and source-updated timestamps, when authoritative
- first-seen, last-seen, and last-verified-open timestamps
- salary range and currency, when published
- parsed required and preferred years of experience
- authorization classification and evidence
- role family and recommended resume label
- fit score, tier, and human-readable reasons
- provenance for every factual field: structured feed, official detail text, or unavailable

`first_seen_at` is the canonical discovery timestamp when a source does not expose a reliable publication date. Greenhouse's update timestamp is stored as an update, not as the original publication time. A Stage 1 candidate from Greenhouse can use the detail endpoint to capture its first-published value.

Recent means published within the preceding 30 calendar days using an authoritative source date. When no authoritative publication date exists, only jobs first discovered after the initial seed qualify, and the alert displays `Posted date: unknown` alongside the first-seen timestamp. Jobs with an authoritative date older than 30 days are withheld unless a later official revision changes a material eligibility field.

A job is considered currently open only when it appears in a complete active-feed snapshot or its official detail endpoint returns an active record. A job is marked closed after two consecutive complete snapshots omit it, or immediately when an official detail endpoint returns 404 or 410. Failed, partial, unexplained-empty, or implausibly shrunken snapshots never close jobs. An explicitly source-reported, complete zero-job snapshot may count toward closure.

Jobs are deduplicated across sources using company plus authoritative posting ID when shared, otherwise a normalized official application URL. A conservative company, normalized-title, and normalized-location fallback identifies likely duplicates for review without merging uncertain records. A closed role that later receives a new official posting ID is a repost and may alert again; the same ID reopening alerts only if it was closed for at least seven days or materially changed.

When a job's content hash changes, it is re-evaluated. It is alerted again only when the change moves it into a higher recommendation tier or materially changes its title, employment type, required experience, work-authorization classification, location, or compensation. A compensation change is material when a bound changes by at least 10 percent or the range crosses the $100,000 target.

## Matching and Verification

Matching uses two stages.

### Stage 1: inexpensive eligibility gate

- Require a target role-family signal in the title.
- Reject internships and clearly senior, staff, principal, lead, manager, director, or executive titles.
- Require a structured United States location, a recognized state or metro, or an explicitly United States remote designation. Blank or worldwide remote locations are uncertain and do not qualify for immediate alerts.
- Require full-time employment. Confirm it from structured source data or the official detail text; withhold contract, temporary, part-time, internship, and unresolved employment types.

### Stage 2: detail enrichment and scoring

Every job that passes Stage 1 receives detail-page enrichment before alerting. The evaluator parses responsibilities, minimum qualifications, experience, salary, and work authorization.

- Required zero to three years is eligible. A posting with no numerical minimum remains eligible only when its title and responsibility level are clearly early-career; its experience status is labeled unresolved and receives no more than 10 of 15 experience points.
- A hard required minimum above three years is rejected. A four-year statement is treated as flexible only when the official posting labels it preferred or typical, gives a range that includes three, or explicitly allows education or equivalent experience to reduce the effective minimum to three or less. Such an exception must score at least 85 before the experience component, is capped at `Moderate`, and clearly flags the experience gap. Preferred experience above three years reduces qualification coverage but is not itself a hard rejection.
- Confirmed citizenship, security-clearance, U.S.-person, ITAR, or incompatible export-control requirements are blocked.
- General export-compliance language without a personal eligibility requirement is marked uncertain rather than automatically blocked.
- Explicit no-sponsorship-now-or-in-the-future language is blocked because it conflicts with the candidate's future sponsorship need.
- `Authorized to work at hire` alone is not interpreted as a sponsorship prohibition.
- Work authorization uses four explicit states: confirmed support, OPT-compatible with future sponsorship uncertain, unknown, and blocked. Only direct official posting text can establish confirmed support or a block. OPT compatibility is a tracker inference based on the candidate's stated authorization and must be labeled as such.
- Missing sponsorship information is labeled unknown, not represented as confirmed support.

The versioned scoring model starts at version 1 and totals 100 points after all hard gates pass:

- role and responsibility alignment: 0 to 25
- demonstrated tools and technical evidence: 0 to 15
- required-experience fit: 0 to 15
- robotics, electromechanical, test, or systems-domain alignment: 0 to 10
- required-qualification coverage: 0 to 10
- work-authorization outlook: 0 to 10
- compensation: 0 to 10
- recency: 0 to 5

Work authorization receives 10 points for confirmed support, 7 for OPT-compatible with future sponsorship uncertain, and 4 when unknown; blocked roles fail the gate. Compensation receives 10 points for confirmed $100,000-plus base pay, 6 when only the upper bound reaches $100,000, 3 when unpublished, and 0 when the published upper bound is below $100,000. Annual base pay is compared directly. Hourly base pay is annualized at 2,080 hours; bonuses, equity, overtime, and one-time awards are excluded. A role is labeled `confirmed $100k+` only when the published base-range minimum is at least $100,000, `possible $100k+` when the maximum reaches it, `below target` when the maximum is below it, and `not published` otherwise.

The recommendation mapping is `Apply Now` for 85 to 100, `Strong` for 75 to 84, `Moderate` for 55 to 74, and `Skip` below 55 or whenever a hard gate fails. Strong immediate alerts therefore include both `Apply Now` and `Strong`. Scores, match reasons, missing qualifications, and CV selection are explicitly labeled tracker inference rather than confirmed employer facts.

A configurable role-to-resume map assigns the most relevant existing CV without combining the CVs into a generic profile. The alert names the recommended CV and the role-family evidence behind that choice. Resume files are never uploaded or transmitted by the tracker.

### Role-to-resume map

| Role family or title signal | Exact recommended CV filename | Selection note |
| --- | --- | --- |
| Robotics Systems Engineer, Robotics Integration Engineer, Systems Integration Engineer, Sensor Integration Engineer | `Muhammad Ahmed Robotics Sensing Integration Engineer.pdf` | Default for sensing, bring-up, cross-domain integration, and system debugging |
| Robotics Test Engineer, Hardware Validation Engineer, Optimus system validation | `Mechatronics Engineer, Optimus Hardware Validation .pdf` | Default for integrated robotic-hardware verification and validation |
| Hardware Test Engineer, Electromechanical Test Engineer | `Muhammad Ahmed Hardware Test Engineer.pdf` | Default for fixtures, instrumentation, reliability, failure analysis, and hardware test |
| Electronics Test Engineer, board validation, embedded hardware validation | `Muhammad Ahmed Electronics Test Engineer.pdf` | Use when circuit boards, buses, oscilloscopes, or electrical validation dominate |
| Mechatronics Engineer, Robotics Hardware Engineer, Mechanical Engineer Robotics | `Muhammad Ahmed Mechanical Engineer Robotics Hardware.pdf` | Default for electromechanical design, mechanisms, sensors, motors, and prototype integration |
| Mechanical Engineer Actuators, actuator or transmission design | `Muhammad Ahmed Mechanical Engineer Actuators.pdf` | Use only when actuator and mechanism design is central |
| Mechanical Test Engineer for humanoid robotics | `Muhammad Ahmed Mechanical Test Engineer Figure.pdf` | Use for humanoid mechanical characterization and validation |
| Mechanical Test Engineer for aviation, delivery systems, or flight hardware | `Muhammad Ahmed Mechanical Test Engineer Zipline.pdf` | Use when the product context matches aerial or delivery hardware |
| Camera, optical, or imaging-mechanical integration | `Muhammad Ahmed Mechanical Engineer Camera Optical Zipline.pdf` | Use when camera packaging, optical alignment, or imaging hardware dominates |
| Embedded Systems Engineer, Robotics Embedded Engineer | `Embedded_Software_Engineer.pdf` | Default for C/C++, firmware, microcontrollers, communications, and embedded integration |
| Junior Embedded Engineer or explicit early-career firmware role | `Muhammad Ahmed Junior Embedded Engineer.pdf` | Use when the title and scope are explicitly junior or new graduate |
| Manufacturing Test Engineer, manufacturing validation, automated test equipment | `Muhammad Ahmed Manufacturing Test Engineer Tesla Optimus.pdf` | Default for production test, fixtures, automation, process validation, and root-cause work |
| Automation Engineer or Controls Engineer | `Muhammad Ahmed Robotics Sensing Integration Engineer.pdf` | Use for system controls and sensing; use the manufacturing-test CV instead when factory automation dominates |
| Robotics Software Engineer, ROS 2, MoveIt 2 | `Muhammad Ahmed Robotics Software Engineer Fluidstack.pdf` | Use when robotics software and motion-planning integration dominate |
| Medical Robotics Software Engineer | `Muhammad Ahmed Software Engineer Medical Robotics.pdf` | Use only for medical robotics software roles |
| Robotics ML Systems Engineer | `Muhammad Ahmed Robotics ML Systems Engineer.pdf` | Use only when ML systems and robotics deployment are both core |
| Mechanical Product Development Engineer for robotic hardware | `Muhammad Ahmed Mechanical Engineer Robotics Hardware.pdf` | Default for general robotic product development; actuator and optical overrides above take precedence |

There is no generic CV fallback. If a role does not map confidently, it is withheld from automated alerts and surfaced in validation output for a manual mapping decision.

## State and Migration

State schema version 2 separates discovery, evaluation, and delivery. A matching job is not marked delivered until Telegram confirms success. Failed alerts remain pending and are retried on the next run.

The migration reads the existing state but writes a compact structure containing:

- active jobs and recently changed candidates
- delivered-alert identities and content revisions
- pending alerts
- moderate digest queue and last digest date
- per-source health, circuit-breaker state, and last successful snapshot

The migration preserves alerted identities needed to avoid duplicate notifications. Old nonmatching records are dropped from the working state after migration but remain recoverable from Git history. Retention is bounded, and state is written atomically through a temporary file followed by replacement. Invalid JSON causes a hard failure instead of silently resetting to an empty state.

The persisted state never stores full job descriptions. It retains only hashes and compact evaluation evidence with these version-1 limits:

- current active posting IDs for each source, replaced after every complete snapshot
- candidate records for active jobs plus closed candidates for 90 days
- delivered posting revisions for 365 days, capped at the 10,000 most recent revisions
- pending alerts until delivered, or for 30 days after the role is confirmed closed
- moderate-digest entries for 30 days
- the latest 30 health events per source

The limits are configurable and deterministic. A synthetic-load test representing 25,000 active source IDs, 5,000 candidate revisions, and 10,000 delivered identities must keep serialized state below 5 MB. Normal operation targets less than 2 MB.

The workflow commits state only when a queued or delivered alert changes, the active-ID roster changes, candidate or digest state changes, a health or circuit state transitions, or a once-daily health checkpoint is due. Routine fetch timestamps alone do not create a commit, and a no-change heartbeat produces no commit.

## Telegram Delivery

Live mode fails fast when either Telegram secret is missing. Dry-run mode never sends messages, never mutates production state, and never reports a simulated message as delivered.

Strong matches are grouped into digest-style Telegram messages, split only at job boundaries below Telegram's 4,096-character limit. Each entry includes:

- company and exact title
- location and work arrangement
- source-posted date and first-seen time
- salary, when published
- experience requirement
- authorization classification
- fit score and short match explanation
- important gap
- recommended CV
- official application link

Confirmed employer facts carry their source provenance. Salary, experience, authorization, posted date, and full-time status display `Confirmed`, `Not published`, or `Unresolved` as appropriate. Fit score, match explanation, gaps, and recommended CV display `Tracker assessment` so inference cannot be mistaken for employer-provided information.

Telegram sends use bounded retry with `Retry-After`, exponential backoff, and a conservative one-message-per-second chat pace. Only transient network, 429, and 5xx failures are retried. Authentication and permission failures stop the workflow. Error messages are sanitized. Individual fields are truncated safely before message assembly, while the company, title, priority, and official application link are always preserved. A single job entry therefore cannot exceed the message limit.

A successful API response records delivery for that exact message chunk immediately. If a later chunk fails, only the unsent chunk and remaining jobs stay pending. This provides at-least-once delivery, accepting a rare duplicate after a process crash rather than losing a time-sensitive job.

The daily moderate digest uses the same format and includes only unreported moderate matches. After 7:30 PM New York time, each queued moderate revision is processed once. An empty day advances the processed date without sending a message. A failed send does not advance the date or consume its entries. If scheduled runs are missed across midnight, undelivered entries from the preceding 30 days are combined into the next successful digest. Persistent high-priority source failures are summarized once per day without flooding the chat.

## Workflow Behavior

The GitHub Actions workflow will:

1. Check out the repository and install pinned dependencies.
2. Run the automated test suite before any live scan.
3. Scan sources and classify snapshot health.
4. Normalize, deduplicate, enrich, and score candidate jobs.
5. Queue strong alerts and the daily moderate digest.
6. Deliver Telegram batches and record only confirmed successes.
7. Atomically save compact state.
8. Commit meaningful state changes even when an alert failure causes a nonzero final status.
9. Surface source-health and delivery failures clearly in the Actions summary.

All scheduled and manual live runs share one production concurrency group with `cancel-in-progress: false`, so state writers and Telegram delivery cannot overlap. Validation and dry-run jobs use temporary state and may run separately. Before committing state, the workflow pulls and rebases the default branch. A rejected state push is retried up to three times by reloading remote state and applying the current run's deterministic delta: delivery identities are unioned, the newest timestamp wins for candidate and health records, the newest complete `fetched_at` wins for source inventories, and pending or digest entries are removed only by a recorded successful delivery. It never force-pushes.

Manual inputs will include `validate_only`, `dry_run`, and `seed`. Validation checks configuration, source schemas, and Telegram credentials without sending a message. Seed mode initializes current active jobs without alerting. These modes use temporary or explicitly selected state and cannot accidentally consume production alerts.

## Testing

The print-only test script is replaced by a pytest suite covering:

- title, seniority, structured location, and United States remote rules
- the Lausanne/`usa` false-positive regression
- zero-to-three-year parsing and required-versus-preferred experience
- citizenship, clearance, U.S.-person, ITAR, export, and sponsorship classification
- freshness, content revisions, complete versus partial snapshots, and closure rules
- state migration, bounded retention, atomic writes, and corrupt-state failure
- source health, empty responses, retries, pagination, and circuit-breaker recovery
- Telegram formatting, escaping, size splitting, pacing, retry classes, and secret validation
- delivery success, partial failure, pending retries, and no lost alerts
- dry-run and validation modes producing no production-state mutation

Network tests use recorded fixtures or mocked responses. A manual validation workflow may call Telegram `getMe` and `getChat`, which validate credentials without sending a message. One real end-to-end test alert requires explicit confirmation immediately before it is sent.

## Internal-Browser Review and Deployment

Implementation will occur on the `codex/job-tracker-alerts-v2` branch. The repository, branch, workflow runs, and final review will be opened in the Codex internal browser. No job applications, recruiter messages, or unrelated external actions are part of this work.

Deployment proceeds only after tests pass and the implementation branch is reviewed. The workflow declares `contents: write`, verifies that `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` are available without printing them, and is merged into the repository's default branch because scheduled GitHub Actions use the default branch. The first default-branch run uses seed or validation mode so existing roles do not flood Telegram. After validation, a single real test message is sent only after explicit action-time confirmation, then the twice-hourly schedule is enabled and one scheduled run is observed in the internal browser.

## Success Criteria

- Every enabled source reports fetch health as healthy, empty-valid, partial, or failed, and separately reports circuit state as closed, open, or half-open.
- High-priority companies use validated official sources and links.
- For an on-time workflow, at least 95 percent of eligible strong matches are delivered in the same run and within 10 minutes of fetch completion. GitHub scheduling lag is measured and reported separately. The user-facing expectation remains 15 to 45 minutes for healthy twice-hourly sources; Tesla is explicitly best-effort while its public endpoint blocks automated access.
- Moderate matches are delivered once daily after 7:30 PM New York time.
- A failed Telegram send cannot permanently consume an alert.
- No confirmed incompatible citizenship, clearance, ITAR, or export-controlled role is alerted.
- No non-U.S. job passes through location substrings such as `Lausanne` containing `usa`.
- Dry-run and validation modes do not mutate production state.
- State remains compact and reviewable, with a target below 2 MB in normal operation and a tested 5 MB maximum for the defined synthetic load.
- The automated test suite fails the workflow on a regression.

## Known Constraints

- GitHub Actions scheduling is best-effort, so minute-level delivery cannot be guaranteed.
- Company-specific endpoints can change without notice. Health checks, quarantine, and daily probes reduce silent failures but cannot eliminate maintenance.
- Sponsorship support is frequently unpublished. The tracker must preserve uncertainty rather than infer support.
- Salary is not consistently published. Missing compensation reduces confidence but does not automatically exclude an otherwise strong role.
