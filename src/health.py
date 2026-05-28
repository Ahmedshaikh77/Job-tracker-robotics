"""Show health of all enabled companies based on recent state.

Usage:
    python -m src.health
"""
from __future__ import annotations
import json
import sys
import yaml


def main():
    try:
        with open("config.yaml") as f:
            cfg = yaml.safe_load(f)
    except FileNotFoundError:
        print("config.yaml not found")
        sys.exit(1)

    try:
        with open("state.json") as f:
            state = json.load(f)
    except FileNotFoundError:
        state = {"company_failures": {}, "seen_jobs": {}}

    failures = state.get("company_failures", {})
    seen = state.get("seen_jobs", {})

    enabled = [c for c in cfg["companies"] if c.get("enabled", True)]
    disabled = [c for c in cfg["companies"] if not c.get("enabled", True)]

    # Count jobs we've seen per company
    seen_by_company = {}
    for key in seen:
        if ":" in key:
            company = key.split(":", 1)[0]
            seen_by_company[company] = seen_by_company.get(company, 0) + 1

    print(f"=== Health Report ===")
    print(f"Total companies in config: {len(cfg['companies'])}")
    print(f"  Enabled: {len(enabled)}")
    print(f"  Disabled: {len(disabled)}")
    print(f"Total jobs ever seen: {len(seen)}")
    print()

    print("=== Enabled companies ===")
    healthy = []
    failing = []
    new = []
    for c in enabled:
        name = c["name"]
        fail_count = failures.get(name, {}).get("count", 0)
        job_count = seen_by_company.get(name, 0)
        if fail_count >= 6:
            failing.append((name, fail_count, failures.get(name, {}).get("last_error", "")))
        elif fail_count > 0:
            new.append((name, fail_count, job_count))
        else:
            healthy.append((name, job_count))

    print(f"\n  HEALTHY ({len(healthy)}):")
    for n, jc in sorted(healthy, key=lambda x: -x[1]):
        print(f"    ✓ {n:35s} {jc} jobs tracked")

    if new:
        print(f"\n  RECENTLY FAILING ({len(new)}) - will auto-skip if it hits 6 failures:")
        for n, fc, jc in new:
            print(f"    ⚠ {n:35s} {fc} failures, {jc} jobs tracked")

    if failing:
        print(f"\n  AUTO-DISABLED ({len(failing)}) - hit failure threshold:")
        for n, fc, err in failing:
            print(f"    ✗ {n:35s} {fc} failures")
            print(f"      last error: {err[:100]}")
        print(f"\n  To re-try: edit state.json and remove these entries from 'company_failures',")
        print(f"  or fix the config and let it recover.")

    print(f"\n=== Disabled companies ({len(disabled)}) ===")
    for c in disabled:
        print(f"  - {c['name']} ({c['fetcher']})")


if __name__ == "__main__":
    main()
