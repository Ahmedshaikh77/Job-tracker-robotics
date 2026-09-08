# Engineering job tracker

Official-career-source monitoring for Muhammad Ahmed Nazir Shaikh's early-career US engineering search. The tracker sends useful matches to Telegram, with the appropriate role-specific CV. It does not apply to jobs, contact employers, or invent experience.

Deployment status: the upgrade is installed, the initial baseline is saved, and Telegram live alerts are enabled. Connection validation and an isolated delivery test passed. See [deployment status and verification evidence](docs/deployment-status.md).

## How alerts work

- Requested scans: **every 15 minutes, at minutes 7, 22, 37, and 52**, through GitHub Actions.
- **Apply Now (85+) and Strong (75–84)**: sent after a successful scan verifies the official job details.
- **Moderate (55–74)**: collected for the first successful run after **7:30 PM America/New_York**. This normally means the requested 7:37 PM run; daylight saving changes are handled automatically.
- **Skip (below 55 or any hard eligibility failure)**: no alert.
- No new qualifying role means no routine message. Persistent problems with high-priority sources get a separate daily health summary.

GitHub schedules are best effort. They may start late or skip a slot, so these are requested check times, not guaranteed arrival times. A roughly 15–45 minute discovery-to-alert interval is an operating target, not a promise. The Actions summary separates exact Actions queue lag, estimated cron-slot offset, missed-slot gaps, and fetch-to-delivery time. A run with no newly eligible strong jobs has an N/A delivery-rate result, not a fabricated success.

## What qualifies

The role must be in the United States, confirmed full-time, and relevant to the configured engineering families. Internships, disallowed senior titles, incompatible citizenship/clearance/export-control requirements, and explicit refusal of needed future sponsorship are excluded.

The experience target is 0–3 years. A role above that range is withheld unless its published requirements are clearly flexible and the non-experience fit is unusually strong; any qualifying exception is capped at Moderate. Missing or ambiguous requirements are not treated as proven matches.

Published USD compensation at or above $100,000 ranks highest. A range crossing that target is labeled as possible rather than guaranteed. Unpublished compensation remains explicitly unknown. Salary is never invented or converted from an unknown pay period.

Recent official posting dates are preferred, with a 30-day freshness window. An unknown posting date is shown as unknown alongside the tracker's first-seen date. Old roles are not called newly posted merely because the tracker first encounters them. During the first complete scan of each source, existing listings form a baseline without flooding Telegram. Later genuinely new listings and qualifying material changes can alert.

To receive useful jobs immediately after setup, manually enable **current_roundup** with a dry-run preview, then a live scan. This freshly verifies the official listings and queues at most **10 unsent current matches**, ranked by fit, for immediate delivery. It includes eligible Moderate matches without waiting for the evening digest. A seeded listing with no published posting date may qualify for this explicit roundup only, with no recency bonus and no claim that it is newly posted. Known old posting dates, incompatible authorization, unresolved employment, and excessive required experience are still withheld. Existing delivery history is preserved. The option defaults to false and is never enabled by the automatic schedule. Selecting it again may send another batch of still-unsent matches, not resend recorded deliveries.

Every alert includes the company, exact title, location/work arrangement, posting date or first seen, salary, experience, employment status, work-authorization evidence, fit score, main gap, recommended CV, and official application link. Published facts are labeled with their source; fit and CV selection are tracker assessments. Sponsorship that is not explicitly supported by the posting remains uncertain. **Visa sponsorship does not imply green-card support.** Confirm that separately with the employer.

## Sources

The configured roster contains 42 enabled sources:

| Platform | Companies |
| --- | --- |
| Greenhouse | Figure, Apptronik, Nimble, Neuralink, Kodiak, Agility Robotics, Waymo, Formlabs, Torc Robotics, May Mobility, Nuro, Zipline, Diligent Robotics, Viam, Path Robotics, Carbon Robotics |
| Ashby | Applied Intuition, 1X, Matic, Fab2, Persona AI, Skydio, Aurora, Standard Bots, Cobot, Gecko Robotics, Bedrock Robotics, Physical Intelligence, Serve Robotics, Generalist |
| Lever | Zoox, Shield AI, Pickle Robot, Robust AI, Field AI, Dexterity |
| Other official sources | Intuitive, Chef Robotics, Boston Dynamics, Foundation, Amazon Robotics US, Tesla |

Foundation is provisional. Tesla is best effort because the careers interface can change or reject automated requests. Both remain visible in health reporting but do not block initial activation. Apple is explicitly disabled, not silently represented as working. A configured source is not a guarantee of current coverage: inspect the latest source-health report for the actual result.

CV routing lives in `profile.yaml`. It retains separate resume filenames for embedded software, electronics/hardware test, robotics hardware, sensing integration, manufacturing test, mechanical test, camera/optical work, and the other supported families. A job without a valid role-to-CV route is withheld.

## Reliability and privacy

1. **Prepare:** fetch complete inventories, verify shortlisted details, assess eligibility, and create a durable queue. No Telegram messages are sent.
2. **Commit:** validate an immutable state delta and merge it onto the latest default-branch state with a normal Git push.
3. **Deliver:** reload that committed queue, remove stale items, send job-boundary-safe message chunks, and save a receipt after each chunk.
4. **Sync receipts:** preserve earlier successes even if a later message fails. Retries do not intentionally resend recorded receipts.

Delivery is at least once. A crash after Telegram accepts a message but before its receipt is durably saved may duplicate that chunk. The tracker favors recoverability over silently losing later messages.

Incomplete or anomalously shrunken feeds cannot close jobs. Two complete omissions, or an official closed/404/410 detail result, can close a listing. After three consecutive source failures the circuit pauses normal fetching and tries a daily recovery probe; it is not permanently disabled.

State schema 2 keeps compact identities, facts needed for comparison, queues, and receipts. It does not store full job descriptions. Closed candidates are retained for 90 days, undelivered entries for 30 days, receipts for 365 days subject to a 10,000-record cap, and run ledgers for 14 days. Schema-1 migration preserves previously alerted identities while discarding old bulk nonmatching records. Credentials stay in GitHub Actions secrets, never in the repository or recovery artifacts.

## GitHub setup and controls

Repository **Settings → Secrets and variables → Actions**:

- Secret `TELEGRAM_BOT_TOKEN`: the bot token from Telegram BotFather.
- Secret `TELEGRAM_CHAT_ID`: the destination chat. Start the bot in Telegram first.
- Variable `JOB_TRACKER_LIVE_ENABLED`: exactly `false` during setup; exactly `true` enables live operation.

The **Actions → Check Jobs → Run workflow** menu offers:

| Mode | Purpose | Sends messages? |
| --- | --- | --- |
| `validate-only` | Validate config/source schemas and Telegram `getMe`/`getChat` | No |
| `dry-run` | Preview assessments without changing production state | No |
| `seed` | Migrate state and baseline existing listings | No |
| `smoke-test` | Send the exact supplied test payload without scanning or changing state | One |
| `live` | Prepare, commit, deliver, and sync | Only queued eligible alerts/digests |
| `recover-delivery` | Retry one explicitly identified persisted immediate queue while live is disabled | Only that run's remaining jobs |

Safe activation order: leave live disabled, pass tests, validate, seed all required sources, approve and send the isolated test message, then enable live. Live refuses to start when required source seed markers are missing. Production writes and sends must use the repository's default branch. One non-cancelling concurrency group prevents overlapping production runs.

GitHub may disable scheduled workflows after 60 days of repository inactivity. Re-enable **Check Jobs** on its Actions page, run validation, and confirm the activation variable. The workflow operates in GitHub's hosted environment; the laptop and browser do not need to remain open.

The owner's Codex task also has a **Keep job alerts scanning** backup check every 30 minutes. It requests a normal GitHub live scan only when actual committed source-scan state is over 45 minutes old and no scan is already active. It respects disabled live alerts and never reseeds, sends test messages, or enables current_roundup. This additional backup depends on the local Codex host and authenticated GitHub access being available; it is not a cloud uptime guarantee. Normal successful scans stay quiet in Codex because job alerts arrive in Telegram.

## Local development

```sh
python -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pytest -q
.venv/bin/python -m src.main --mode dry-run --state state.json
.venv/bin/python -m src.main --mode dry-run --current-roundup --state state.json
.venv/bin/python -m src.health --state state.json
```

Tests forbid unmocked network requests. `dry-run` is a live read-only source check, not an offline test. Stateful CLI modes require a run ID and delta output:

```sh
python -m src.main --mode seed --state /tmp/tracker-state.json --run-id local-seed --delta-json /tmp/seed.delta.json --report-json /tmp/seed.report.json
python -m src.main --mode live --phase prepare --state /tmp/tracker-state.json --run-id local-1 --delta-json /tmp/queue.delta.json
python -m src.reporting summarize --state state.json --run-id gha:RUN:ATTEMPT --output /tmp/tracker-report.json
```

Do not use a temporary local state file as the production merge base. The workflow helpers replay deltas against the newest committed default branch without refetching jobs or resending messages during a push retry.

## Recovery and rollback

For queue-sync failure, leave live disabled, download the sanitized delta artifact, and apply it to a fresh default-branch checkout:

```sh
python -m src.state_merge --state state.json --delta /path/to/queue.delta.json --output /tmp/recovered-state.json
```

Inspect the validated state diff, copy the result into `state.json`, and make a normal non-force push. Only then, after confirming the exact original queued run ID, manually select `recover-delivery`, supply that run ID, and enter `SEND PENDING`. Recovery performs no scan, evaluation, moderate digest, or health-summary send.

For receipt-sync failure, replay and commit the receipt delta **before** retrying delivery. If that records all successful receipts, no recovery send is needed. Never apply an unverified or mismatched delta. Artifacts expire after 14 days.

For rollback, set live to `false`, restore the last known-good workflow and state together through a reviewed Git commit, and validate again before reactivation. Do not force-push or erase current receipt history without reconciling it.
