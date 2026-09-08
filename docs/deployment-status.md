# Deployment status

Activation verified September 8, 2026, New York time.

## Completed

- [Upgrade PR #1](https://github.com/Ahmedshaikh77/Job-tracker-robotics/pull/1) merged into main. Subsequent safe connection diagnostics are also on main.
- 508 automated tests passed both locally and in GitHub Actions.
- [Initial baseline run](https://github.com/Ahmedshaikh77/Job-tracker-robotics/actions/runs/34183189667) succeeded: 5,154 source listings fetched, 193 assessments completed, zero alerts queued or delivered.
- State schema 2 is committed. All 40 required sources and Foundation have baseline markers. Existing alert history was migrated, not reset.
- All 40 required source inventories are healthy in persisted state. Tesla's automated endpoint is unavailable and is not represented as working. Apple is explicitly disabled.
- Amazon's initial detail-failure category represented 63 pages with multiple U.S. locations. The verified parser fix preserves every official U.S. location without relaxing country/provenance rules. The [final GitHub baseline verification](https://github.com/Ahmedshaikh77/Job-tracker-robotics/actions/runs/34183736040) succeeded and cleared all 63 Amazon detail retries. Only Tesla remains in the source-failure report. Immediate and moderate alert queues remain empty.
- Telegram secrets remain in GitHub's encrypted secret storage. No token or chat ID was exposed in logs or reports.

## Activation

The previous saved bot token failed validation. The owner supplied its replacement, which was saved directly to GitHub's encrypted Actions secret without putting it in source files or command arguments.

- [Connection and source validation](https://github.com/Ahmedshaikh77/Job-tracker-robotics/actions/runs/34230706283) succeeded. Telegram getMe/getChat passed, 5,151 source listings were fetched read-only, and all required sources passed. Only best-effort Tesla was unavailable.
- [Isolated Telegram delivery test](https://github.com/Ahmedshaikh77/Job-tracker-robotics/actions/runs/34230854985) succeeded at 9:15 AM New York time: one test message delivered, no job scan, no state mutation, and no job application submitted.
- `JOB_TRACKER_LIVE_ENABLED=true` is confirmed through GitHub's API, and the Check Jobs workflow is active.
- A dispatch started before the setting was saved safely skipped live work. The [first enabled live scan](https://github.com/Ahmedshaikh77/Job-tracker-robotics/actions/runs/34231068487) succeeded after the saved value was verified. It fetched 3,251 returned listings, completed 191 assessments, and queued no new immediate or moderate alerts. Unchanged inventories remained cached. State was committed as `93e7eb5`, and the delivery and receipt-sync phases succeeded with no backlog.

Before activation, the persisted schema-2 state passed validation, all 40 required sources were seeded, and the immediate, moderate, and health queues were empty. The full offline suite passed 508 tests locally and in GitHub.

For future token replacement, use [GitHub's secure secret editor](https://github.com/Ahmedshaikh77/Job-tracker-robotics/settings/secrets/actions/TELEGRAM_BOT_TOKEN). Enter only the token, not a URL or a `bot` prefix. Do not put it in a public issue or source file. Rotating the token in BotFather also requires updating this GitHub secret.

## After activation

GitHub requests a scan at minutes 17 and 47 of every hour. Strong matches are sent after verification; moderate matches are grouped on the first successful run after 7:30 PM New York time. GitHub can delay scheduled runs, so these are requested times rather than guaranteed delivery times. The laptop and browser can be closed.

Actual job-alert latency for a nonzero newly eligible scheduled run remains unverified until a real new match. A zero-denominator run must be reported as N/A, never as a successful delivery-rate test.
