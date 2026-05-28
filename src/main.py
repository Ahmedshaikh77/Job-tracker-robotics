"""Main orchestrator: fetch all companies concurrently, filter, alert on new, save state."""
from __future__ import annotations
import argparse
import logging
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import yaml

from .alerts import TelegramNotifier
from .fetchers import get_fetcher
from .filters import JobFilter
from .models import Job
from .state import StateManager

log = logging.getLogger(__name__)

# After this many consecutive failures, auto-skip a company until it recovers
# (resets on first success, or when you manually edit state.json).
FAILURE_SKIP_THRESHOLD = 6


def setup_logging():
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def load_config(path: str) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def fetch_one(company: dict) -> tuple[dict, list[Job], str | None]:
    """Fetch a single company. Returns (company, jobs, error_msg)."""
    name = company.get("name", "?")
    if not company.get("enabled", True):
        return company, [], None
    fetcher_name = company.get("fetcher")
    if not fetcher_name:
        return company, [], f"{name}: no fetcher specified"
    try:
        fetcher = get_fetcher(fetcher_name)
        t0 = time.time()
        jobs = fetcher.fetch(company)
        dt = time.time() - t0
        log.info("✓ %s: %d jobs (%.1fs)", name, len(jobs), dt)
        return company, jobs, None
    except Exception as e:
        err = f"{name} ({fetcher_name}): {type(e).__name__}: {e}"
        log.warning("✗ %s", err)
        return company, [], err


def run(config_path: str, state_path: str, dry_run: bool = False, seed: bool = False) -> int:
    cfg = load_config(config_path)
    companies = cfg.get("companies", [])
    state = StateManager(state_path)

    # Auto-skip companies with too many recent consecutive failures
    skipped_for_failures = []
    enabled = []
    for c in companies:
        if not c.get("enabled", True):
            continue
        fails = state.consecutive_failures(c["name"])
        if fails >= FAILURE_SKIP_THRESHOLD:
            skipped_for_failures.append((c["name"], fails))
            continue
        enabled.append(c)

    log.info("Starting run: %d companies enabled, %d auto-skipped for repeated failures",
             len(enabled), len(skipped_for_failures))
    if skipped_for_failures:
        for n, f in skipped_for_failures:
            log.warning("  skipping %s (%d consecutive failures)", n, f)

    job_filter = JobFilter(cfg)
    max_workers = cfg.get("max_workers", 10)

    all_jobs: list[Job] = []
    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = [pool.submit(fetch_one, c) for c in enabled]
        for f in as_completed(futures):
            company, jobs, err = f.result()
            if err:
                errors.append(err)
                state.record_company_failure(company["name"], err)
            else:
                state.record_company_success(company["name"])
            all_jobs.extend(jobs)

    log.info("Fetched %d jobs total", len(all_jobs))

    if seed:
        log.info("SEED MODE: marking all %d jobs as seen without alerting", len(all_jobs))
        state.seed_existing(all_jobs)
        state.save()
        return 0

    new_jobs = state.filter_new(all_jobs)
    log.info("%d new jobs (not previously seen)", len(new_jobs))

    relevant: list[tuple[Job, int]] = []
    for j in new_jobs:
        passed, score, _bd = job_filter.passes(j)
        state.mark_seen(j, alerted=passed)
        if passed:
            relevant.append((j, score))

    relevant.sort(key=lambda x: (-x[1], x[0].company, x[0].title))
    log.info("%d new jobs match filter", len(relevant))
    for j, s in relevant[:50]:
        log.info("  [%d] %s @ %s | %s", s, j.title, j.company, j.location)

    sent = 0
    if not dry_run and relevant:
        notifier = TelegramNotifier()
        sent = notifier.alert_batch(relevant)
        log.info("Sent %d Telegram alerts", sent)

    state.save()
    return 0


def main():
    parser = argparse.ArgumentParser(description="Job tracker")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--state", default="state.json")
    parser.add_argument("--dry-run", action="store_true", help="Don't send Telegram messages")
    parser.add_argument("--seed", action="store_true",
                        help="First-run: mark all existing jobs as seen without alerting")
    args = parser.parse_args()

    setup_logging()
    sys.exit(run(args.config, args.state, dry_run=args.dry_run, seed=args.seed))


if __name__ == "__main__":
    main()
