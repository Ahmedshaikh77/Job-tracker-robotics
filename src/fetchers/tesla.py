"""Fail-closed best-effort monitor for Tesla's protected official endpoint."""
from __future__ import annotations

from ..models import DetailResult, DetailStatus, FetchContext, FetchResult, Job
from .base import Fetcher, failed_fetch, http_status, register, utc_now


TESLA_ENDPOINT = "https://www.tesla.com/cua-api/apps/careers/state"


def parse_tesla_payload(payload: object) -> tuple[Job, ...] | None:
    """Quarantine unknown schemas until an official success capture is reviewed."""
    return None


@register
class TeslaFetcher(Fetcher):
    name = "tesla"

    def fetch(self, company: dict, context: FetchContext) -> FetchResult:
        try:
            payload = self.http.get_json(TESLA_ENDPOINT).data
        except Exception as exc:
            status = http_status(exc)
            suffix = f" ({status})" if status is not None else ""
            return failed_fetch(
                context, f"Tesla official endpoint unavailable{suffix}"
            )
        if parse_tesla_payload(payload) is None:
            return failed_fetch(context, "Tesla official endpoint schema mismatch")
        return failed_fetch(context, "Tesla official endpoint schema mismatch")

    def fetch_detail(self, company: dict, job: Job) -> DetailResult:
        return DetailResult(
            job=None,
            status=DetailStatus.FAILED,
            fetched_at=utc_now(),
            error="Tesla official detail schema is not verified",
        )
