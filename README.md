# Job Tracker (Robotics / Mechanical / Medical-Device edition)

Polls 50+ company career pages every 5 minutes, filters new postings against robotics/mechanical/mechatronics/controls/embedded and medical-device R&D criteria (new grad / early career / 2026 / 2027, US-based), and pings you on Telegram the moment a match appears.

Includes an international-student safety filter that auto-drops roles requiring US citizenship, security clearance, or ITAR compliance (see `hard_exclude_anywhere` in config.yaml).

Runs entirely on free-tier GitHub Actions. No server. No cost.

---

## How it works

```
GitHub Actions cron (every 5 min)
        │
        ▼
   src/main.py
        │
        ├──► fetchers/ ──► Greenhouse, Lever, Ashby, Workday, SmartRecruiters,
        │                  Oracle Cloud, Amazon, Google, Microsoft, Apple,
        │                  Meta, IBM, Bloomberg, Deloitte, Accenture
        │
        ▼
   filters.py  (score-based: keywords + level signals + location)
        │
        ▼
   state.json  (dedupe: only fires once per job)
        │
        ▼
   Telegram bot ──► your phone
```

State is committed back to the repo after each run so it survives across runs.

---

## Setup (one-time, ~15 min)

### 1. Create a Telegram bot

1. On Telegram, search for **@BotFather** and start a chat.
2. Send `/newbot`, follow the prompts, get a **bot token** that looks like `1234567890:AAH...`.
3. Send any message to your new bot (you must initiate the chat or it can't message you).
4. Open this URL in a browser (replace `<TOKEN>`):
   `https://api.telegram.org/bot<TOKEN>/getUpdates`
5. Find your **chat_id** in the JSON response — it's the `"id"` field under `"chat"`. Looks like a 9–10 digit number.

Save the bot token and chat_id — you'll paste them into GitHub Secrets in step 4.

### 2. Push this repo to GitHub

```bash
cd job-tracker
git init
git add .
git commit -m "initial commit"
gh repo create job-tracker --public --source=. --push
```

Or via the GitHub UI: create a **public** repo (needed for free unlimited Actions minutes), then push.

### 3. Enable Actions write permissions

In the new repo: **Settings → Actions → General → Workflow permissions** → select **"Read and write permissions"** → Save.

This lets the workflow commit the updated `state.json` back to the repo.

### 4. Add Telegram secrets

**Settings → Secrets and variables → Actions → New repository secret**, add two:

- `TELEGRAM_BOT_TOKEN` — the bot token from step 1
- `TELEGRAM_CHAT_ID` — your chat_id from step 1

### 5. Seed the state (first run, no spam)

On first run there are thousands of existing jobs. Without seeding you'd get a massive flood. Seed mode marks every current job as "already seen" without alerting:

**Actions tab → Check Jobs → Run workflow** → toggle **seed** to `true` → **Run workflow**.

Wait for it to complete (~30 sec). After this, only *brand-new* postings will trigger alerts.

### 6. You're done

The cron now runs every 5 min. New matching jobs land in Telegram within ~5 min of being posted.

---

## Verifying Workday & Oracle Cloud companies

Many companies in `config.yaml` are marked `enabled: false` because their Workday tenant/site values need verification. To enable one:

1. Visit the company's careers page (e.g. `https://goldmansachs.wd1.myworkdayjobs.com/...`).
2. The URL pattern is `https://{host}/en-US/{site}` — copy the `host` and `site`.
3. Open Chrome DevTools → Network tab → reload the page → find the request to `wday/cxs/{tenant}/{site}/jobs`.
4. Update the entry in `config.yaml` and set `enabled: true`.
5. Test locally: `python -m src.main --dry-run` (filters but doesn't send Telegram).

Same idea for Oracle Cloud companies (JPMorgan, Oracle) — look for `siteNumber` in the network tab.

---

## Tuning the filter

Edit `config.yaml` under `filter:`.

- **`include_keywords`** — keywords that boost relevance (add domain terms you care about)
- **`exclude_keywords`** — title words that disqualify outright (seniority, wrong job families)
- **`level_signals_positive`** — phrases that signal early-career fit (boost score)
- **`locations`** — substrings allowed in the location field
- **`min_score`** — minimum score to alert (default 3, raise to be stricter)

The scoring:
- +3 per include keyword in title
- +1 per include keyword in description
- +2 per level signal anywhere
- −5 per exclude keyword in title (usually disqualifies on its own)

Run `python test_filter.py` to validate changes against the test cases.

---

## Adding more companies

```yaml
- name: SomeCompany
  fetcher: greenhouse   # or lever / ashby / workday / smartrecruiters / oracle_cloud
  slug: somecompany     # for greenhouse/lever/ashby/smartrecruiters
  enabled: true
```

How to figure out the ATS:
- URL contains `boards.greenhouse.io/{slug}` → `greenhouse`, slug after the path
- URL contains `jobs.lever.co/{slug}` → `lever`
- URL contains `jobs.ashbyhq.com/{slug}` → `ashby`
- URL contains `myworkdayjobs.com` → `workday` (need host + tenant + site, see above)
- URL contains `jobs.smartrecruiters.com/{slug}` → `smartrecruiters`
- URL contains `oraclecloud.com` → `oracle_cloud` (need host + site_number)

For sites that use none of these (totally bespoke), add a new fetcher in `src/fetchers/custom.py`.

---

## Local testing

```bash
pip install -r requirements.txt
export TELEGRAM_BOT_TOKEN=...
export TELEGRAM_CHAT_ID=...

# Dry run - fetch + filter, log what would be sent, don't actually message
python -m src.main --dry-run

# Real run
python -m src.main

# Seed (mark all current jobs seen, don't alert)
python -m src.main --seed

# Test a single company in isolation (great for fixing a Workday config)
python -m src.verify "Anthropic"
python -m src.verify "Bank of America" --show 20

# See which companies are healthy / failing / auto-disabled
python -m src.health
```

---

## Self-healing: auto-disable on repeated failures

If a company's fetcher errors out **6 runs in a row** (≈30 minutes), the orchestrator stops calling that company until you fix the config. This keeps the run fast and clean even if a company changes their careers site. State is in `state.json` under `company_failures`. To re-enable after fixing config: delete that company's entry under `company_failures`, or just push a successful config — the counter resets on the first success.

Run `python -m src.health` to see what's healthy vs failing.

---

## Troubleshooting

- **No alerts ever** → check Actions tab for run logs. Common cause: forgot to seed (step 5) so state was already populated, or no companies matched filter yet. Try `--dry-run` to see scoring. Run `python -m src.health` to see fetch health per company.
- **Spammed with old jobs on first run** → you skipped seeding. Quick fix: delete `state.json` content (replace with `{"seen_jobs":{},"company_failures":{}}`), then run with seed=true.
- **Telegram silent** → verify `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` secrets are set, and that you've sent your bot at least one message to initiate the chat.
- **A company errors every run** → it'll be auto-disabled after 6 consecutive failures (shows up in `health` output). Then check the workflow logs to see the actual error, fix the Workday `host`/`tenant`/`site` (use `python -m src.verify "Company Name"` to test), and the counter resets on first success.
- **Actions stops running after ~60 days of repo inactivity** → GitHub disables scheduled workflows in dormant repos. Push any commit to wake it up.

---

## Files

```
job-tracker/
├── .github/workflows/check_jobs.yml   # cron + workflow
├── config.yaml                         # all companies + filter rules
├── state.json                          # jobs already seen (auto-updated)
├── requirements.txt
├── test_filter.py                      # sanity test for filter logic
└── src/
    ├── main.py                         # orchestrator
    ├── models.py                       # Job dataclass
    ├── filters.py                      # filter / scoring
    ├── state.py                        # state management
    ├── alerts.py                       # Telegram sender
    └── fetchers/                       # per-ATS fetchers
        ├── base.py
        ├── greenhouse.py
        ├── lever.py
        ├── ashby.py
        ├── workday.py
        ├── smartrecruiters.py
        ├── oracle_cloud.py
        └── custom.py                   # Amazon, Google, MS, Apple, Meta, IBM, Bloomberg, Deloitte, Accenture
```
