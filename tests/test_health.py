from __future__ import annotations

from src.config import (
    AppSettings,
    DigestPolicy,
    FetchPolicy,
    MatchingPolicy,
    SourceConfig,
    StatePolicy,
    TelegramPolicy,
)
from src.health import build_health_report


def _settings() -> AppSettings:
    sources = (
        SourceConfig("Figure", "greenhouse", True, "validated", "high", True, slug="figureai"),
        SourceConfig("Empty", "greenhouse", True, "validated", "normal", True, slug="empty"),
        SourceConfig("Partial", "greenhouse", True, "validated", "normal", True, slug="partial"),
        SourceConfig("Tesla", "tesla", True, "best-effort", "high", False, board="careers"),
        SourceConfig("Missing", "greenhouse", True, "validated", "normal", True, slug="missing"),
        SourceConfig("Apple", "unavailable", False, reason="official endpoint not validated"),
    )
    return AppSettings(
        FetchPolicy(), StatePolicy(), MatchingPolicy(), DigestPolicy(), TelegramPolicy(), sources
    )


def test_health_report_separates_fetch_health_from_circuit():
    state = {
        "sources": {
            "greenhouse:figureai": {
                "health": "healthy",
                "circuit": "closed",
                "active_ids": ["123"],
                "last_complete_at": "2026-09-07T12:00:00+00:00",
                "next_probe_at": None,
                "warnings": [],
            },
            "greenhouse:empty": {
                "health": "empty-valid",
                "circuit": "closed",
                "active_ids": [],
                "last_complete_at": "2026-09-07T12:00:00+00:00",
                "next_probe_at": None,
                "warnings": [],
            },
            "greenhouse:partial": {
                "health": "partial",
                "circuit": "open",
                "active_ids": ["old"],
                "last_complete_at": "2026-09-06T12:00:00+00:00",
                "next_probe_at": "2026-09-08T12:00:00+00:00",
                "warnings": ["pagination incomplete"],
            },
            "tesla:careers": {
                "health": "failed",
                "circuit": "open",
                "active_ids": [],
                "last_complete_at": None,
                "next_probe_at": "2026-09-08T12:00:00+00:00",
                "warnings": ["protected endpoint"],
            },
        }
    }

    report, has_required_failure = build_health_report(_settings(), state)

    assert "Figure | health=healthy | circuit=closed" in report
    assert "Empty | health=empty-valid | circuit=closed" in report
    assert "Tesla | health=failed | circuit=open" in report
    assert "Missing | health=missing | circuit=unknown" in report
    assert "Apple | disabled | reason=official endpoint not validated" in report
    assert "5 monitored, 1 disabled" in report
    assert has_required_failure is True


def test_nonrequired_failure_is_visible_but_does_not_block():
    settings = AppSettings(
        FetchPolicy(),
        StatePolicy(),
        MatchingPolicy(),
        DigestPolicy(),
        TelegramPolicy(),
        (
            SourceConfig(
                "Tesla", "tesla", True, "best-effort", "high", False, board="careers"
            ),
        ),
    )
    report, failed = build_health_report(
        settings,
        {"sources": {"tesla:careers": {"health": "failed", "circuit": "open"}}},
    )
    assert "Tesla | health=failed | circuit=open" in report
    assert failed is False
