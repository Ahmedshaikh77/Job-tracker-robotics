"""State management - tracks which jobs we've already alerted on,
and per-company consecutive-failure counts for auto-disable."""
from __future__ import annotations
import json
import os
from datetime import datetime, timezone
from typing import Iterable
from .models import Job


class StateManager:
    def __init__(self, path: str):
        self.path = path
        self.state = {"seen_jobs": {}, "company_failures": {}}
        if os.path.exists(path):
            with open(path) as f:
                try:
                    self.state = json.load(f)
                except json.JSONDecodeError:
                    self.state = {"seen_jobs": {}, "company_failures": {}}
        if "seen_jobs" not in self.state:
            self.state["seen_jobs"] = {}
        if "company_failures" not in self.state:
            self.state["company_failures"] = {}

    def is_seen(self, job: Job) -> bool:
        return job.key in self.state["seen_jobs"]

    def mark_seen(self, job: Job, alerted: bool = False) -> None:
        if job.key not in self.state["seen_jobs"]:
            self.state["seen_jobs"][job.key] = {
                "first_seen": datetime.now(timezone.utc).isoformat(),
                "title": job.title,
                "location": job.location,
                "url": job.url,
                "alerted": alerted,
            }

    def filter_new(self, jobs: Iterable[Job]) -> list[Job]:
        return [j for j in jobs if not self.is_seen(j)]

    def record_company_success(self, company_name: str) -> None:
        self.state["company_failures"].pop(company_name, None)

    def record_company_failure(self, company_name: str, error: str) -> int:
        entry = self.state["company_failures"].get(company_name, {"count": 0, "last_error": ""})
        entry["count"] = entry.get("count", 0) + 1
        entry["last_error"] = error[:300]
        entry["last_failed_at"] = datetime.now(timezone.utc).isoformat()
        self.state["company_failures"][company_name] = entry
        return entry["count"]

    def consecutive_failures(self, company_name: str) -> int:
        return self.state["company_failures"].get(company_name, {}).get("count", 0)

    def save(self) -> None:
        if len(self.state["seen_jobs"]) > 50000:
            items = sorted(
                self.state["seen_jobs"].items(),
                key=lambda kv: kv[1].get("first_seen", ""),
                reverse=True,
            )[:50000]
            self.state["seen_jobs"] = dict(items)
        with open(self.path, "w") as f:
            json.dump(self.state, f, indent=2, sort_keys=True)

    def seed_existing(self, jobs: Iterable[Job]) -> None:
        for j in jobs:
            self.mark_seen(j, alerted=False)
