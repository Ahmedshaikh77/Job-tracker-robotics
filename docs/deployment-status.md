# Deployment status

Verified September 8, 2026 UTC (September 7 in New York).

## Completed

- [Upgrade PR #1](https://github.com/Ahmedshaikh77/Job-tracker-robotics/pull/1) merged into main. Subsequent safe connection diagnostics are also on main.
- 508 automated tests passed both locally and in GitHub Actions.
- [Initial baseline run](https://github.com/Ahmedshaikh77/Job-tracker-robotics/actions/runs/34183189667) succeeded: 5,154 source listings fetched, 193 assessments completed, zero alerts queued or delivered.
- State schema 2 is committed. All 40 required sources and Foundation have baseline markers. Existing alert history was migrated, not reset.
- All 40 required source inventories are healthy in persisted state. Tesla's automated endpoint is unavailable and is not represented as working. Apple is explicitly disabled.
- Amazon's initial detail-failure category represented 63 pages with multiple U.S. locations. The verified parser fix preserves every official U.S. location without relaxing country/provenance rules. The [final GitHub baseline verification](https://github.com/Ahmedshaikh77/Job-tracker-robotics/actions/runs/34183736040) succeeded and cleared all 63 Amazon detail retries. Only Tesla remains in the source-failure report. Immediate and moderate alert queues remain empty.
- Telegram secrets remain in GitHub's encrypted secret storage. No token or chat ID was exposed in logs or reports.

## Remaining activation step

[Connection validation](https://github.com/Ahmedshaikh77/Job-tracker-robotics/actions/runs/34183322368) failed before fetching or sending. Telegram's getMe operation returned HTTP/API 404. This identifies the saved bot connection as invalid or malformed; it does not establish whether the destination chat is valid.

The owner must replace `TELEGRAM_BOT_TOKEN` with the correct token from Telegram's verified BotFather in [GitHub's secure secret editor](https://github.com/Ahmedshaikh77/Job-tracker-robotics/settings/secrets/actions/TELEGRAM_BOT_TOKEN). Enter only the token, not a URL or a `bot` prefix. Do not paste it into a public issue, source file, or chat transcript.

Then rerun `validate-only`. If it passes, approve the isolated test message, verify delivery, and set `JOB_TRACKER_LIVE_ENABLED` to `true`. The proposed test payload is pending owner confirmation: "Job tracker test: Telegram is connected. No job application has been submitted."

The workflow itself is enabled, but `JOB_TRACKER_LIVE_ENABLED=false` prevents live scanning and delivery. No alert arrival time can be promised until activation succeeds.

## After activation

GitHub requests a scan at minutes 17 and 47 of every hour. Strong matches are sent after verification; moderate matches are grouped on the first successful run after 7:30 PM New York time. GitHub can delay scheduled runs, so these are requested times rather than guaranteed delivery times. The laptop and browser can be closed.

Actual live-delivery latency and a nonzero newly eligible scheduled run remain unverified until activation and a real new match. A zero-denominator run must be reported as N/A, never as a successful delivery-rate test.
