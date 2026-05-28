"""Test a single company in isolation. Run before flipping a company to enabled.

Usage:
    python -m src.verify "Anthropic"
    python -m src.verify "Goldman Sachs"
"""
from __future__ import annotations
import argparse
import logging
import sys
import yaml

from .fetchers import get_fetcher
from .filters import JobFilter

logging.basicConfig(level="INFO", format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(description="Verify one company's fetcher works")
    parser.add_argument("name", help="Company name (must match config.yaml)")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--show", type=int, default=10, help="How many jobs to show")
    parser.add_argument("--show-passing", action="store_true",
                        help="Only show jobs that pass the filter")
    args = parser.parse_args()

    with open(args.config) as f:
        cfg = yaml.safe_load(f)

    company = None
    for c in cfg["companies"]:
        if c["name"].lower() == args.name.lower():
            company = c
            break
    if not company:
        print(f"No company '{args.name}' in config.")
        print(f"Available: {', '.join(c['name'] for c in cfg['companies'])}")
        sys.exit(1)

    print(f"Testing fetch for: {company['name']} (fetcher: {company['fetcher']})")
    print(f"Config: {company}")
    print()

    try:
        fetcher = get_fetcher(company["fetcher"])
        jobs = fetcher.fetch(company)
    except Exception as e:
        print(f"❌ FETCH FAILED: {type(e).__name__}: {e}")
        print()
        print("Common fixes:")
        print("  - For Workday: check host, tenant, site values match the company's URL")
        print("    e.g. https://{host}/en-US/{site} - extract from careers page URL")
        print("  - For Greenhouse/Lever/Ashby/SmartRecruiters: check the slug")
        print("  - For Oracle Cloud: check host and site_number")
        sys.exit(2)

    print(f"✅ FETCH OK - got {len(jobs)} jobs")
    print()

    f = JobFilter(cfg)
    passing = []
    failing = []
    for j in jobs:
        ok, score, bd = f.passes(j)
        (passing if ok else failing).append((j, score, bd))

    print(f"After filter: {len(passing)} passing, {len(failing)} filtered out")
    print()

    to_show = passing if args.show_passing else (passing + failing)
    print(f"Showing {min(args.show, len(to_show))} jobs:")
    print("-" * 100)
    for j, score, _ in to_show[:args.show]:
        flag = "✓" if (j, score, _) in passing else " "
        print(f"  {flag} [{score:>3}] {j.title[:55]:<55} | {j.location[:30]:<30} | {j.url[:50]}")


if __name__ == "__main__":
    main()
