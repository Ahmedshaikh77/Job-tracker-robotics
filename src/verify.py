"""Read-only validation of configured official job sources."""
from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import datetime, timezone
from typing import Callable

from .config import (
    AppSettings,
    ConfigError,
    build_health_policy,
    build_retry_policy,
    build_revision_policy,
    build_state_limits,
    load_config,
)
from .fetchers import get_fetcher, source_key
from .fetchers.http import HttpClient
from .models import DetailStatus, FetchHealth, Job
from .state import StateCorruptionError, StateManager


def _clean(value: object) -> str:
    return " ".join(str(value).split())[:300]


_DETAIL_KEYWORDS = (
    "robot",
    "mechan",
    "mechatron",
    "embedded",
    "test",
    "hardware",
    "system",
    "integration",
    "sensor",
    "control",
    "manufacturing",
)


def _detail_candidate(jobs: tuple[Job, ...]) -> Job:
    return next(
        (
            job
            for job in jobs
            if any(keyword in job.title.casefold() for keyword in _DETAIL_KEYWORDS)
        ),
        jobs[0],
    )


def run_verification(
    settings: AppSettings,
    state: StateManager,
    *,
    fetcher_factory: Callable = get_fetcher,
    http: HttpClient | None = None,
    now: datetime | None = None,
    show: int = 3,
    detail_sample: bool = False,
) -> tuple[str, int]:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise ValueError("verification time must be timezone aware")
    shared_http = http or HttpClient(retry=build_retry_policy(settings))
    lines = ["Official source verification"]
    required_failure = False
    for source in settings.companies:
        if not source.enabled:
            lines.append(f"{source.name} | disabled | reason={_clean(source.reason)}")
            continue
        mapping = source.as_fetcher_mapping()
        key = source_key(mapping)
        existing = state.state.get("sources", {}).get(key, {})
        circuit = existing.get("circuit", "closed") if isinstance(existing, dict) else "closed"
        try:
            fetcher = fetcher_factory(source.fetcher, http=shared_http)
            result = fetcher.fetch(mapping, state.fetch_context(key, current))
        except Exception as exc:
            lines.append(
                f"{source.name} | health=failed | complete=false | pages=0 | "
                f"total=unknown | circuit={circuit} | warning={_clean(type(exc).__name__)}"
            )
            if source.required_for_validation:
                required_failure = True
            continue
        total = "unknown" if result.source_total is None else str(result.source_total)
        lines.append(
            f"{source.name} | health={result.health.value} | "
            f"complete={str(result.complete).lower()} | pages={result.pages_fetched} | "
            f"total={total} | circuit={circuit} | required="
            f"{str(source.required_for_validation).lower()}"
        )
        warnings = (*result.warnings, *((result.error,) if result.error else ()))
        for warning in warnings:
            lines.append(f"  warning={_clean(warning)}")
        for job in result.jobs[: max(0, show)]:
            lines.append(
                f"  sample={_clean(job.title)} | {_clean(job.location)} | "
                f"{_clean(job.url)}"
            )
        if detail_sample and result.jobs:
            candidate = _detail_candidate(result.jobs)
            try:
                detail = fetcher.fetch_detail(mapping, candidate)
                lines.append(
                    f"  detail={detail.status.value} | title={_clean(candidate.title)} | "
                    f"url={_clean(candidate.url)}"
                )
                detail_warnings = (
                    *detail.warnings,
                    *((detail.error,) if detail.error else ()),
                )
                for warning in detail_warnings:
                    lines.append(f"  detail_warning={_clean(warning)}")
                if (
                    source.required_for_validation
                    and detail.status is not DetailStatus.HEALTHY
                ):
                    required_failure = True
            except Exception as exc:
                lines.append(
                    f"  detail=failed | title={_clean(candidate.title)} | "
                    f"warning={_clean(type(exc).__name__)}"
                )
                if source.required_for_validation:
                    required_failure = True
        if source.required_for_validation and (
            result.health not in {FetchHealth.HEALTHY, FetchHealth.EMPTY_VALID}
            or not result.complete
            or circuit != "closed"
        ):
            required_failure = True
    return "\n".join(lines), 1 if required_failure else 0


def parse_args(argv: list[str] | None = None):
    parser = argparse.ArgumentParser(description="Validate official job sources")
    parser.add_argument("name", nargs="?", help="optional configured company name")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--state", default="state.json")
    parser.add_argument("--show", type=int, default=3)
    parser.add_argument(
        "--detail-sample",
        action="store_true",
        help="verify one role-relevant official detail record per nonempty source",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        settings = load_config(args.config)
        if args.name:
            selected = tuple(
                source
                for source in settings.companies
                if source.name.casefold() == args.name.casefold()
            )
            if not selected:
                print(f"No configured company named {_clean(args.name)}")
                return 2
            settings = replace(settings, companies=selected)
        state = StateManager.load(
            args.state,
            build_state_limits(settings),
            health_policy=build_health_policy(settings),
            revision_policy=build_revision_policy(settings),
        )
        text, status = run_verification(
            settings,
            state,
            show=args.show,
            detail_sample=args.detail_sample,
        )
    except (ConfigError, StateCorruptionError, OSError, ValueError) as exc:
        print(f"Verification configuration/state error: {_clean(exc)}")
        return 2
    print(text)
    return status


if __name__ == "__main__":
    raise SystemExit(main())
