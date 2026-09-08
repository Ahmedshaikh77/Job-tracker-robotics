# Deployment status

Activation and alert-repair work, September 8, 2026, New York time.

## Immediate Moderate alerts

The owner approved sending qualifying Moderate matches after each scan instead of waiting for the evening digest. Commit `b8697af` enables `digest.moderate_immediate: true`, bypassing only the evening and once-daily delivery gates. Existing queued Moderate entries use the same persisted queue, stale-item checks, per-message receipts, and duplicate protection. Job filters, baseline history, credentials, and daily health-summary timing were not changed.

[Live verification run 34273221857](https://github.com/Ahmedshaikh77/Job-tracker-robotics/actions/runs/34273221857) ran that commit successfully with normal live mode and `current_roundup=false`. All 604 tests passed locally and in GitHub Actions. Preparation fetched 2,384 returned listings and assessed 173. Delivery sent the previously queued Skydio **Systems Integration and Test Engineer** alert; Telegram accepted it at **4:11:49 PM New York time on September 8**, message ID 10. Receipt-sync commit `27dc2ba` preserves its original queued run identity and records the successful delivery. Both job queues are empty, all 252 prior receipt records are unchanged, and the new receipt brings the total to 253. This confirms Telegram acceptance, not whether the user has read the message.

The implementation was independently reviewed. Tests cover existing-queue release before evening, another qualifying match later the same day, replay without duplicate sends, receipt-delta merge/replay, delivery failure retention, dirty-state rejection, unchanged job filters and health timing, and opting back into the evening digest. Known separate issues remain: GitHub's best-effort scan timing, Tesla's blocked automated source, and the employment parser's handling of "non-internship" qualification wording. These were not changed as part of this delivery-timing request.

## Telegram readability and qualification cleanup

Commits `2996bbb` and `e586bd3` simplify future Telegram cards while preserving source provenance internally. Cards retain job IDs, exact titles, work arrangement/full-time status, salary, required/preferred years, sponsorship caveats, fit, the main gap, CV and application link. Internal role/CV-choice rows and raw qualification tokens no longer appear in the message. Existing Telegram messages are not edited or resent merely to change their formatting.

Degree assessment now recognizes an explicit broad technical/engineering alternative supported by the documented engineering degree. Unconfirmed related-field or equivalent-experience paths remain review items and earn no automatic credit. Explicit mismatches, authorization restrictions, experience limits, full-time requirements and freshness rules remain enforced.

All 591 tests passed locally. Independent review covered formatting, degree parsing, state compatibility, message chunking and receipt safety. A read-only live preview fetched 2,633 returned listings, assessed 178, and rendered five current Amazon cards without sending messages or changing the production state file. A final targeted check of Amazon requisition 10503216 confirmed that its degree warning was corrected, its card contained no internal degree token, and its new assessment did not trigger a repeat alert. Delivery history and state schema were not reset.

[Live deployment verification run](https://github.com/Ahmedshaikh77/Job-tracker-robotics/actions/runs/34240343479) completed successfully on `e586bd3`, using normal live mode and `current_roundup=false`. All 591 tests also passed in GitHub Actions. The scan fetched 2,632 returned listings and assessed 177, then safely committed state in `217d007`. It found no new alertable revisions, so zero messages were sent. Both pending queues remain empty, all nine prior roundup receipts remain saved, and the total 252 receipts are preserved. The live-enable setting remains true. This verifies deployment and a successful normal live scan; it does not claim a new Telegram delivery when no job was eligible to send.

## Job-alert repair

**Production verification:** [Roundup run 34237751963](https://github.com/Ahmedshaikh77/Job-tracker-robotics/actions/runs/34237751963) completed successfully on repair commit `5697b5a`. All 548 tests passed locally and in GitHub Actions. The live prepare phase fetched 5,150 returned listings, assessed 255, and queued nine current matches. Telegram accepted all nine listings in five messages between 10:23:57 and 10:24:02 AM New York time on September 8. The receipt-sync phase committed all nine delivery receipts in `624fa44`. Immediate and moderate queues are empty. The nine listings represent five Amazon requisitions, one Field AI role, one Pickle Robot role, and two Zoox roles, all labeled Moderate rather than overstated as strong fits.

This proves real job delivery and durable receipt saving for the manual roundup. It does not prove guaranteed future scan timing. Persisted source health is 41 healthy sources and one unavailable source, Tesla. There were no new source-fetch failures in this run because Tesla was already paused until its scheduled recovery probe.

The owner confirmed receipt of the 9:15 AM connection test, but no job listings. Inspection found separate causes: existing listings were intentionally baselined; unknown posting dates on those seeded listings were rejected as stale; Amazon's human-readable posting dates were not normalized for the strict freshness parser; and some legal-age requirements were being read as years of work experience. GitHub also skipped multiple requested schedule slots.

The repair adds an explicit manual current-openings roundup, limited to ten unsent, freshly verified matches. Unknown dates remain unknown and receive no recency credit; known old dates and all other hard eligibility restrictions remain enforced. Amazon dates are normalized without substituting update dates, and explicit legal-age language is not counted as employment experience. Published salary ranges now appear in the messages, not just the $100k-target label.

The requested cloud schedule is now every 15 minutes, at minutes 7, 22, 37, and 52. A separate native Codex backup checks every 30 minutes and requests a normal live scan only when committed source-scan state is over 45 minutes old and no scan is active. The backup requires the local Codex host and authenticated GitHub access. Neither schedule is presented as guaranteed timing.

A read-only live preview fetched 5,150 returned listings and assessed 255, producing nine qualifying current matches with no messages or production-state changes. They included recent Amazon roles and undated, verified openings at Field AI, Pickle Robot, and Zoox. These are current matches, not nine claims of newly published jobs. Tesla remained unavailable under its existing paused-source recovery policy.

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

GitHub requests a scan at minutes 7, 22, 37, and 52 of every hour. Qualifying Apply Now, Strong, and Moderate matches are sent after verification, with no evening wait under the current production setting. Daily health summaries retain the 7:30 PM New York schedule. GitHub can delay scheduled runs, so these are requested times rather than guaranteed delivery times. Cloud scans do not require the laptop or browser to remain open; the additional Codex backup does require its local host to be available.

Manual-roundup delivery is verified above. Actual job-alert latency for a nonzero newly eligible scheduled run remains unverified until a real new match. A zero-denominator run must be reported as N/A, never as a successful delivery-rate test.
