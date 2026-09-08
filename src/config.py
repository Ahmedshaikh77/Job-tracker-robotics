"""Strict runtime configuration for the job tracker."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

import yaml


class ConfigError(ValueError):
    """Raised when configuration cannot be trusted."""


@dataclass(frozen=True, slots=True)
class FetchPolicy:
    max_attempts: int = 3
    connect_timeout_seconds: float = 10.0
    read_timeout_seconds: float = 30.0
    backoff_base_seconds: float = 2.0
    backoff_cap_seconds: float = 60.0
    per_host_pacing_seconds: Decimal = Decimal("0.25")
    circuit_failure_threshold: int = 3
    half_open_probe_hours: int = 24
    shrink_ratio: Decimal = Decimal("0.40")
    shrink_min_previous_count: int = 20


@dataclass(frozen=True, slots=True)
class StatePolicy:
    closed_candidate_days: int = 90
    delivered_days: int = 365
    delivered_limit: int = 10000
    pending_max_age_days: int = 30
    digest_entry_days: int = 30
    health_event_limit: int = 30
    run_ledger_days: int = 14


@dataclass(frozen=True, slots=True)
class MatchingPolicy:
    freshness_days: int = 30
    same_id_reopen_days: int = 7
    compensation_material_change_ratio: Decimal = Decimal("0.10")
    compensation_threshold: Decimal = Decimal("100000")


@dataclass(frozen=True, slots=True)
class DigestPolicy:
    timezone: str = "America/New_York"
    send_after: str = "19:30"
    persistent_health_warning_runs: int = 2
    moderate_immediate: bool = False


@dataclass(frozen=True, slots=True)
class TelegramPolicy:
    max_attempts: int = 3
    timeout_seconds: float = 15.0
    backoff_base_seconds: float = 2.0
    backoff_cap_seconds: float = 60.0
    minimum_send_interval_seconds: float = 1.0
    message_limit: int = 3900


@dataclass(frozen=True, slots=True)
class SourceConfig:
    name: str
    fetcher: str
    enabled: bool = True
    status: str | None = None
    priority: str = "normal"
    required_for_validation: bool = False
    slug: str | None = None
    host: str | None = None
    tenant: str | None = None
    site: str | None = None
    board: str | None = None
    source_id: str | None = None
    search_query: str | None = None
    best_effort: bool | None = None
    reason: str | None = None

    def as_fetcher_mapping(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "name": self.name,
            "fetcher": self.fetcher,
            "enabled": self.enabled,
        }
        for field_name in (
            "slug",
            "host",
            "tenant",
            "site",
            "board",
            "source_id",
            "search_query",
            "best_effort",
            "reason",
        ):
            value = getattr(self, field_name)
            if value is not None:
                result[field_name] = value
        return result


@dataclass(frozen=True, slots=True)
class AppSettings:
    fetch_policy: FetchPolicy
    state_policy: StatePolicy
    matching_policy: MatchingPolicy
    digest: DigestPolicy
    telegram: TelegramPolicy
    companies: tuple[SourceConfig, ...]
    max_workers: int = 12


_IDENTITY_FIELDS: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "greenhouse": ("slug",),
        "ashby": ("slug",),
        "lever": ("slug",),
        "smartrecruiters": ("slug",),
        "gem": ("slug",),
        "workday": ("host", "tenant", "site"),
        "rippling": ("board",),
        "amazon": ("source_id",),
        "tesla": ("board",),
    }
)

_EXPECTED_ROSTER = (
    ("Figure", "greenhouse", "figureai"),
    ("Apptronik", "greenhouse", "apptronik"),
    ("Nimble", "greenhouse", "nimblerobotics"),
    ("Neuralink", "greenhouse", "neuralink"),
    ("Kodiak", "greenhouse", "kodiak"),
    ("Agility Robotics", "greenhouse", "agilityrobotics"),
    ("Waymo", "greenhouse", "waymo"),
    ("Formlabs", "greenhouse", "formlabs"),
    ("Torc Robotics", "greenhouse", "torcrobotics"),
    ("May Mobility", "greenhouse", "maymobility"),
    ("Nuro", "greenhouse", "nuro"),
    ("Zipline", "greenhouse", "flyzipline"),
    ("Diligent Robotics", "greenhouse", "diligentrobotics"),
    ("Viam", "greenhouse", "viamrobotics"),
    ("Path Robotics", "greenhouse", "pathrobotics"),
    ("Carbon Robotics", "greenhouse", "carbonrobotics"),
    ("Applied Intuition", "ashby", "applied"),
    ("1X", "ashby", "1x"),
    ("Matic", "ashby", "Maticrobots"),
    ("Fab2", "ashby", "Fab2"),
    ("Persona AI", "ashby", "persona.ai"),
    ("Skydio", "ashby", "skydio"),
    ("Aurora", "ashby", "aurora-operations-inc"),
    ("Standard Bots", "ashby", "standardbots"),
    ("Cobot", "ashby", "cobot"),
    ("Gecko Robotics", "ashby", "gecko-robotics"),
    ("Bedrock Robotics", "ashby", "bedrock-robotics"),
    ("Physical Intelligence", "ashby", "physicalintelligence"),
    ("Serve Robotics", "ashby", "serverobotics"),
    ("Generalist", "ashby", "generalist"),
    ("Zoox", "lever", "zoox"),
    ("Shield AI", "lever", "shieldai"),
    ("Pickle Robot", "lever", "picklerobot"),
    ("Robust AI", "lever", "robust-ai"),
    ("Field AI", "lever", "field-ai"),
    ("Dexterity", "lever", "dexterity"),
    ("Intuitive", "smartrecruiters", "Intuitive"),
    ("Chef Robotics", "gem", "chef-robotics"),
    ("Boston Dynamics", "workday", "bostondynamics.wd1.myworkdayjobs.com:bostondynamics:Boston_Dynamics"),
    ("Foundation Robotics", "rippling", "foundation-robotics"),
    ("Amazon Robotics", "amazon", "robotics-us"),
    ("Tesla", "tesla", "careers"),
)


def _mapping(value: Any, field_name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConfigError(f"{field_name} must be a mapping")
    return dict(value)


def _real_int(value: Any, field_name: str, *, minimum: int = 1) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ConfigError(f"{field_name} must be an integer of at least {minimum}")
    return value


def _positive_number(value: Any, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise ConfigError(f"{field_name} must be a positive number")
    number = float(value)
    if number <= 0:
        raise ConfigError(f"{field_name} must be a positive number")
    return number


def _decimal(
    value: Any,
    field_name: str,
    *,
    maximum: Decimal | None = None,
) -> Decimal:
    if isinstance(value, bool):
        raise ConfigError(f"{field_name} must be a positive finite decimal")
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ConfigError(f"{field_name} must be a positive finite decimal") from None
    if not parsed.is_finite() or parsed <= 0 or (maximum is not None and parsed > maximum):
        raise ConfigError(f"{field_name} must be a positive finite decimal")
    return parsed


def _policy(raw: dict[str, Any], key: str, defaults: dict[str, Any]) -> dict[str, Any]:
    value = raw.get(key, {})
    values = _mapping(value, key)
    unknown = set(values) - set(defaults)
    if unknown:
        raise ConfigError(f"{key} has unknown fields: {sorted(unknown)}")
    return {**defaults, **values}


def _parse_source(value: Any, index: int) -> SourceConfig:
    row = _mapping(value, f"companies[{index}]")
    allowed = set(SourceConfig.__dataclass_fields__)
    unknown = set(row) - allowed
    if unknown:
        raise ConfigError(f"companies[{index}] has unknown fields: {sorted(unknown)}")
    if not isinstance(row.get("name"), str) or not row["name"].strip():
        raise ConfigError(f"companies[{index}].name must be nonempty")
    if not isinstance(row.get("fetcher"), str) or not row["fetcher"].strip():
        raise ConfigError(f"companies[{index}].fetcher must be nonempty")
    if "enabled" in row and not isinstance(row["enabled"], bool):
        raise ConfigError(f"companies[{index}].enabled must be Boolean")
    if row.get("enabled", True):
        if not isinstance(row.get("required_for_validation"), bool):
            raise ConfigError(
                f"companies[{index}].required_for_validation must be Boolean"
            )
        required = _IDENTITY_FIELDS.get(row["fetcher"], ())
        for field_name in required:
            if not isinstance(row.get(field_name), str) or not row[field_name].strip():
                raise ConfigError(
                    f"companies[{index}].{field_name} is required for {row['fetcher']}"
                )
    return SourceConfig(**row)


def validate_config(settings: AppSettings) -> tuple[str, ...]:
    from .fetchers import available
    from .fetchers.base import source_key

    errors: list[str] = []
    runnable = set(available())
    names: set[str] = set()
    keys: set[str] = set()
    enabled_signature: list[tuple[str, str, str]] = []
    disabled: list[SourceConfig] = []
    for source in settings.companies:
        if source.name in names:
            errors.append(f"duplicate company name: {source.name}")
        names.add(source.name)
        if not source.enabled:
            disabled.append(source)
            continue
        if source.fetcher not in runnable:
            errors.append(f"unknown enabled fetcher: {source.fetcher}")
        if source.status not in {"validated", "provisional", "best-effort"}:
            errors.append(f"invalid status for {source.name}: {source.status}")
        if source.priority not in {"high", "normal"}:
            errors.append(f"invalid priority for {source.name}: {source.priority}")
        if not isinstance(source.required_for_validation, bool):
            errors.append(f"required_for_validation must be Boolean for {source.name}")
        if source.status == "provisional" and source.required_for_validation:
            errors.append(f"provisional source cannot be required: {source.name}")
        mapping = source.as_fetcher_mapping()
        try:
            key = source_key(mapping)
        except (KeyError, ValueError) as exc:
            errors.append(f"invalid source identity for {source.name}: {exc}")
            continue
        if key in keys:
            errors.append(f"duplicate source key: {key}")
        keys.add(key)
        enabled_signature.append((source.name, source.fetcher, key.split(":", 1)[1]))
    if tuple(enabled_signature) != _EXPECTED_ROSTER:
        errors.append("enabled source roster does not match the approved 42-source roster")
    if len(disabled) != 1 or disabled[0].as_fetcher_mapping() != {
        "name": "Apple",
        "fetcher": "unavailable",
        "enabled": False,
        "reason": "official endpoint not validated",
    }:
        errors.append("disabled source roster must contain only the approved Apple descriptor")
    return tuple(errors)


def load_config(path: str | Path) -> AppSettings:
    try:
        raw_value = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"unable to load configuration: {type(exc).__name__}") from None
    raw = _mapping(raw_value, "configuration")
    allowed_top = {
        "max_workers",
        "fetch_policy",
        "state_policy",
        "matching_policy",
        "digest",
        "telegram",
        "companies",
    }
    unknown = set(raw) - allowed_top
    if unknown:
        raise ConfigError(f"configuration has unknown fields: {sorted(unknown)}")
    companies_value = raw.get("companies")
    if not isinstance(companies_value, list):
        raise ConfigError("companies must be a list")

    fetch = _policy(raw, "fetch_policy", {
        "max_attempts": 3,
        "connect_timeout_seconds": 10,
        "read_timeout_seconds": 30,
        "backoff_base_seconds": 2,
        "backoff_cap_seconds": 60,
        "per_host_pacing_seconds": Decimal("0.25"),
        "circuit_failure_threshold": 3,
        "half_open_probe_hours": 24,
        "shrink_ratio": Decimal("0.40"),
        "shrink_min_previous_count": 20,
    })
    state = _policy(raw, "state_policy", {
        "closed_candidate_days": 90,
        "delivered_days": 365,
        "delivered_limit": 10000,
        "pending_max_age_days": 30,
        "digest_entry_days": 30,
        "health_event_limit": 30,
        "run_ledger_days": 14,
    })
    matching = _policy(raw, "matching_policy", {
        "freshness_days": 30,
        "same_id_reopen_days": 7,
        "compensation_material_change_ratio": Decimal("0.10"),
        "compensation_threshold": Decimal("100000"),
    })
    digest = _policy(raw, "digest", {
        "timezone": "America/New_York",
        "send_after": "19:30",
        "persistent_health_warning_runs": 2,
        "moderate_immediate": False,
    })
    telegram = _policy(raw, "telegram", {
        "max_attempts": 3,
        "timeout_seconds": 15,
        "backoff_base_seconds": 2,
        "backoff_cap_seconds": 60,
        "minimum_send_interval_seconds": 1,
        "message_limit": 3900,
    })

    fetch_policy = FetchPolicy(
        max_attempts=_real_int(fetch["max_attempts"], "max_attempts"),
        connect_timeout_seconds=_positive_number(fetch["connect_timeout_seconds"], "connect_timeout_seconds"),
        read_timeout_seconds=_positive_number(fetch["read_timeout_seconds"], "read_timeout_seconds"),
        backoff_base_seconds=_positive_number(fetch["backoff_base_seconds"], "backoff_base_seconds"),
        backoff_cap_seconds=_positive_number(fetch["backoff_cap_seconds"], "backoff_cap_seconds"),
        per_host_pacing_seconds=_decimal(fetch["per_host_pacing_seconds"], "per_host_pacing_seconds"),
        circuit_failure_threshold=_real_int(fetch["circuit_failure_threshold"], "circuit_failure_threshold"),
        half_open_probe_hours=_real_int(fetch["half_open_probe_hours"], "half_open_probe_hours"),
        shrink_ratio=_decimal(fetch["shrink_ratio"], "shrink_ratio", maximum=Decimal("1")),
        shrink_min_previous_count=_real_int(fetch["shrink_min_previous_count"], "shrink_min_previous_count"),
    )
    state_policy = StatePolicy(**{
        key: _real_int(value, key) for key, value in state.items()
    })
    matching_policy = MatchingPolicy(
        freshness_days=_real_int(matching["freshness_days"], "freshness_days"),
        same_id_reopen_days=_real_int(matching["same_id_reopen_days"], "same_id_reopen_days"),
        compensation_material_change_ratio=_decimal(
            matching["compensation_material_change_ratio"],
            "compensation_material_change_ratio",
            maximum=Decimal("1"),
        ),
        compensation_threshold=_decimal(
            matching["compensation_threshold"], "compensation_threshold"
        ),
    )
    if not isinstance(digest["timezone"], str) or not digest["timezone"]:
        raise ConfigError("timezone must be nonempty")
    if not isinstance(digest["send_after"], str) or len(digest["send_after"].split(":")) != 2:
        raise ConfigError("send_after must be HH:MM")
    if not isinstance(digest["moderate_immediate"], bool):
        raise ConfigError("moderate_immediate must be a boolean")
    digest_policy = DigestPolicy(
        timezone=digest["timezone"],
        send_after=digest["send_after"],
        persistent_health_warning_runs=_real_int(
            digest["persistent_health_warning_runs"],
            "persistent_health_warning_runs",
        ),
        moderate_immediate=digest["moderate_immediate"],
    )
    telegram_policy = TelegramPolicy(
        max_attempts=_real_int(telegram["max_attempts"], "telegram.max_attempts"),
        timeout_seconds=_positive_number(telegram["timeout_seconds"], "telegram.timeout_seconds"),
        backoff_base_seconds=_positive_number(telegram["backoff_base_seconds"], "telegram.backoff_base_seconds"),
        backoff_cap_seconds=_positive_number(telegram["backoff_cap_seconds"], "telegram.backoff_cap_seconds"),
        minimum_send_interval_seconds=_positive_number(
            telegram["minimum_send_interval_seconds"],
            "telegram.minimum_send_interval_seconds",
        ),
        message_limit=_real_int(telegram["message_limit"], "telegram.message_limit"),
    )
    settings = AppSettings(
        fetch_policy=fetch_policy,
        state_policy=state_policy,
        matching_policy=matching_policy,
        digest=digest_policy,
        telegram=telegram_policy,
        companies=tuple(_parse_source(value, index) for index, value in enumerate(companies_value)),
        max_workers=_real_int(raw.get("max_workers", 12), "max_workers"),
    )
    errors = validate_config(settings)
    if errors:
        raise ConfigError("; ".join(errors))
    return settings


def build_retry_policy(settings: AppSettings):
    from .fetchers.http import RetryPolicy

    policy = settings.fetch_policy
    return RetryPolicy(
        attempts=policy.max_attempts,
        connect_timeout=policy.connect_timeout_seconds,
        read_timeout=policy.read_timeout_seconds,
        backoff_base=policy.backoff_base_seconds,
        backoff_cap=policy.backoff_cap_seconds,
        per_host_pacing=float(policy.per_host_pacing_seconds),
    )


def build_state_limits(settings: AppSettings):
    from .state import StateLimits

    policy = settings.state_policy
    return StateLimits(
        closed_candidate_days=policy.closed_candidate_days,
        delivered_days=policy.delivered_days,
        delivered_limit=policy.delivered_limit,
        pending_max_age_days=policy.pending_max_age_days,
        digest_entry_days=policy.digest_entry_days,
        health_event_limit=policy.health_event_limit,
        run_ledger_days=policy.run_ledger_days,
    )


def build_health_policy(settings: AppSettings):
    from datetime import timedelta

    from .source_health import SourceHealthPolicy

    policy = settings.fetch_policy
    return SourceHealthPolicy(
        failure_threshold=policy.circuit_failure_threshold,
        probe_interval=timedelta(hours=policy.half_open_probe_hours),
        shrink_ratio=policy.shrink_ratio,
        shrink_min_previous_count=policy.shrink_min_previous_count,
    )


def build_revision_policy(settings: AppSettings):
    from .lifecycle import RevisionPolicy

    policy = settings.matching_policy
    return RevisionPolicy(
        same_id_reopen_days=policy.same_id_reopen_days,
        compensation_material_change_ratio=policy.compensation_material_change_ratio,
        compensation_threshold=policy.compensation_threshold,
    )
