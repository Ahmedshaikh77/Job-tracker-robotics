from __future__ import annotations

from dataclasses import FrozenInstanceError
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from src.config import ConfigError, load_config, validate_config
from src.fetchers import available
from src.fetchers.base import source_key
from tests.fakes import load_json_fixture


CONFIG_PATH = Path(__file__).parents[1] / "config.yaml"


def _plain_source(source):
    row = source.as_fetcher_mapping()
    row["source_key"] = source_key(row)
    row["status"] = source.status
    row["priority"] = source.priority
    row["required_for_validation"] = source.required_for_validation
    return row


def _write_changed_config(tmp_path, change):
    raw = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    change(raw)
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    return path


def test_config_contains_exact_ordered_enabled_roster_and_only_disabled_apple():
    settings = load_config(CONFIG_PATH)

    enabled = [_plain_source(source) for source in settings.companies if source.enabled]
    disabled = [source.as_fetcher_mapping() for source in settings.companies if not source.enabled]

    assert enabled == load_json_fixture("source_roster.json")
    assert disabled == [
        {
            "name": "Apple",
            "fetcher": "unavailable",
            "enabled": False,
            "reason": "official endpoint not validated",
        }
    ]
    assert len(enabled) == 42


def test_every_enabled_fetcher_is_registered_and_keys_are_unique():
    settings = load_config(CONFIG_PATH)
    enabled = [source for source in settings.companies if source.enabled]

    assert {source.fetcher for source in enabled} <= set(available())
    assert len({source.name for source in enabled}) == len(enabled)
    assert len({source_key(source.as_fetcher_mapping()) for source in enabled}) == len(enabled)
    assert validate_config(settings) == ()


def test_settings_are_frozen_and_fetcher_mappings_are_fresh():
    settings = load_config(CONFIG_PATH)
    figure = settings.companies[0]
    first = figure.as_fetcher_mapping()
    first["name"] = "changed"

    assert figure.as_fetcher_mapping()["name"] == "Figure"
    with pytest.raises(FrozenInstanceError):
        settings.max_workers = 1


def test_all_runtime_policy_values_are_loaded():
    settings = load_config(CONFIG_PATH)

    assert settings.fetch_policy.max_attempts == 3
    assert settings.fetch_policy.connect_timeout_seconds == 10
    assert settings.fetch_policy.read_timeout_seconds == 30
    assert settings.fetch_policy.per_host_pacing_seconds == Decimal("0.25")
    assert settings.fetch_policy.shrink_ratio == Decimal("0.40")
    assert settings.state_policy.delivered_limit == 10000
    assert settings.state_policy.run_ledger_days == 14
    assert settings.matching_policy.same_id_reopen_days == 7
    assert settings.matching_policy.compensation_material_change_ratio == Decimal("0.10")
    assert settings.matching_policy.compensation_threshold == Decimal("100000")
    assert settings.digest.timezone == "America/New_York"
    assert settings.digest.send_after == "19:30"
    assert settings.telegram.timeout_seconds == 15
    assert settings.telegram.message_limit == 3900


@pytest.mark.parametrize(
    ("field", "bad_value"),
    [
        ("same_id_reopen_days", True),
        ("same_id_reopen_days", 0),
        ("compensation_material_change_ratio", False),
        ("compensation_material_change_ratio", 0),
        ("compensation_material_change_ratio", 1.01),
        ("compensation_material_change_ratio", ".nan"),
        ("compensation_threshold", 0),
        ("compensation_threshold", -1),
        ("compensation_threshold", ".inf"),
    ],
)
def test_invalid_revision_policy_values_fail_at_yaml_boundary(tmp_path, field, bad_value):
    path = _write_changed_config(
        tmp_path,
        lambda raw: raw["matching_policy"].__setitem__(field, bad_value),
    )

    with pytest.raises(ConfigError, match=field):
        load_config(path)


def test_matching_policy_accepts_documented_boundaries(tmp_path):
    def change(raw):
        raw["matching_policy"]["same_id_reopen_days"] = 1
        raw["matching_policy"]["compensation_material_change_ratio"] = 1
        raw["matching_policy"]["compensation_threshold"] = 0.01

    settings = load_config(_write_changed_config(tmp_path, change))

    assert settings.matching_policy.same_id_reopen_days == 1
    assert settings.matching_policy.compensation_material_change_ratio == Decimal("1")
    assert settings.matching_policy.compensation_threshold == Decimal("0.01")


def test_missing_adapter_identity_is_reported(tmp_path):
    def change(raw):
        raw["companies"][0].pop("slug")

    with pytest.raises(ConfigError, match="slug"):
        load_config(_write_changed_config(tmp_path, change))


@pytest.mark.parametrize("value", [True, False])
def test_moderate_immediate_accepts_explicit_booleans(tmp_path, value):
    path = _write_changed_config(tmp_path, lambda raw: raw["digest"].__setitem__("moderate_immediate", value))
    assert load_config(path).digest.moderate_immediate is value


@pytest.mark.parametrize("value", ["false", "true", 0, 1, None])
def test_moderate_immediate_rejects_ambiguous_values(tmp_path, value):
    path = _write_changed_config(tmp_path, lambda raw: raw["digest"].__setitem__("moderate_immediate", value))
    with pytest.raises(ConfigError, match="moderate_immediate"):
        load_config(path)


def test_roster_drift_is_reported(tmp_path):
    def change(raw):
        raw["companies"].append(
            {
                "name": "Unexpected",
                "fetcher": "greenhouse",
                "slug": "unexpected",
                "enabled": True,
                "status": "validated",
                "priority": "normal",
                "required_for_validation": True,
            }
        )

    with pytest.raises(ConfigError) as captured:
        load_config(_write_changed_config(tmp_path, change))

    assert "roster" in str(captured.value).lower()
