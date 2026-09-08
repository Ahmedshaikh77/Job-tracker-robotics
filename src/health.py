"""Deterministic source-health reporting."""
from __future__ import annotations

import argparse
from collections.abc import Mapping
from typing import Any

from .config import (
    AppSettings,
    ConfigError,
    build_health_policy,
    build_revision_policy,
    build_state_limits,
    load_config,
)
from .fetchers import source_key
from .state import StateCorruptionError, StateManager


def _clean(value: object, default: str = "none") -> str:
    if value is None or value == "":
        return default
    return " ".join(str(value).split())[:300]


def build_health_report(
    config: AppSettings, state: Mapping[str, Any]
) -> tuple[str, bool]:
    source_records = state.get("sources", {})
    if not isinstance(source_records, Mapping):
        raise StateCorruptionError("state sources must be an object")
    monitored = [source for source in config.companies if source.enabled]
    disabled = [source for source in config.companies if not source.enabled]
    lines = [
        "Job source health",
        f"{len(monitored)} monitored, {len(disabled)} disabled",
    ]
    required_failure = False
    for source in sorted(monitored, key=lambda item: item.name.casefold()):
        key = source_key(source.as_fetcher_mapping())
        record = source_records.get(key)
        if not isinstance(record, Mapping):
            health = "missing"
            circuit = "unknown"
            active_count = 0
            last_complete = "never"
            next_probe = "none"
            warning = "no persisted source record"
        else:
            health = _clean(record.get("health"), "missing")
            circuit = _clean(record.get("circuit"), "unknown")
            active_ids = record.get("active_ids", [])
            active_count = len(active_ids) if isinstance(active_ids, list) else 0
            last_complete = _clean(record.get("last_complete_at"), "never")
            next_probe = _clean(record.get("next_probe_at"))
            warnings = record.get("warnings", [])
            warning = (
                _clean(warnings[-1])
                if isinstance(warnings, list) and warnings
                else "none"
            )
        required = "true" if source.required_for_validation else "false"
        lines.append(
            f"{source.name} | health={health} | circuit={circuit} | "
            f"active={active_count} | last_complete={last_complete} | "
            f"next_probe={next_probe} | required={required} | warning={warning}"
        )
        if source.required_for_validation and (
            health not in {"healthy", "empty-valid"} or circuit != "closed"
        ):
            required_failure = True
    for source in sorted(disabled, key=lambda item: item.name.casefold()):
        lines.append(
            f"{source.name} | disabled | reason={_clean(source.reason, 'not specified')}"
        )
    return "\n".join(lines), required_failure


def parse_args(
    argv: list[str] | None = None,
    *,
    default_config: str = "config.yaml",
    default_state: str = "state.json",
):
    parser = argparse.ArgumentParser(description="Job source health")
    parser.add_argument("--config", default=default_config)
    parser.add_argument("--state", default=default_state)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        settings = load_config(args.config)
        state = StateManager.load(
            args.state,
            build_state_limits(settings),
            health_policy=build_health_policy(settings),
            revision_policy=build_revision_policy(settings),
        )
        report, has_required_failure = build_health_report(settings, state.state)
    except (ConfigError, StateCorruptionError, OSError, ValueError) as exc:
        print(f"Health configuration/state error: {_clean(exc)}")
        return 2
    print(report)
    return 1 if has_required_failure else 0


if __name__ == "__main__":
    raise SystemExit(main())
