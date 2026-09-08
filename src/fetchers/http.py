"""Bounded, paced HTTP transport for official job sources."""
from __future__ import annotations

import random
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Callable
from urllib.parse import urlsplit

import requests


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    attempts: int = 3
    connect_timeout: float = 10.0
    read_timeout: float = 30.0
    backoff_base: float = 2.0
    backoff_cap: float = 60.0
    per_host_pacing: float = 0.25


@dataclass(frozen=True, slots=True)
class JsonResponse:
    data: dict | list | None
    status_code: int
    etag: str | None
    not_modified: bool


@dataclass(frozen=True, slots=True)
class TextResponse:
    text: str
    status_code: int
    etag: str | None
    not_modified: bool


class HostPacer:
    """Serialize request starts per host across clients sharing this pacer."""

    def __init__(
        self,
        *,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._monotonic = monotonic
        self._sleep = sleep
        self._lock = threading.Lock()
        self._next_allowed: dict[str, float] = {}

    def wait(self, hostname: str, interval: float) -> None:
        if interval <= 0:
            return
        with self._lock:
            now = self._monotonic()
            delay = max(0.0, self._next_allowed.get(hostname, now) - now)
            if delay:
                self._sleep(delay)
                now = self._monotonic()
            self._next_allowed[hostname] = max(
                now, self._next_allowed.get(hostname, now)
            ) + interval


GLOBAL_HOST_PACER = HostPacer()
_RETRYABLE_STATUSES = {429, 500, 502, 503, 504}


class HttpClient:
    def __init__(
        self,
        session: requests.Session | None = None,
        retry: RetryPolicy = RetryPolicy(),
        pacer: HostPacer = GLOBAL_HOST_PACER,
        sleep: Callable[[float], None] = time.sleep,
        jitter: Callable[[], float] = random.random,
    ) -> None:
        self.session = session or requests.Session()
        self.retry = retry
        self.pacer = pacer
        self.host_interval = retry.per_host_pacing
        self.sleep = sleep
        self.jitter = jitter

    def get_json(
        self,
        url: str,
        *,
        params: dict | None = None,
        headers: dict | None = None,
        etag: str | None = None,
    ) -> JsonResponse:
        return self._request_json("GET", url, params=params, headers=headers, etag=etag)

    def get_text(
        self,
        url: str,
        *,
        params: dict | None = None,
        headers: dict | None = None,
        etag: str | None = None,
    ) -> TextResponse:
        return self._request_text("GET", url, params=params, headers=headers, etag=etag)

    def post_json(
        self,
        url: str,
        *,
        payload: dict,
        headers: dict | None = None,
    ) -> JsonResponse:
        return self._request_json("POST", url, payload=payload, headers=headers)

    def _headers(self, headers: dict | None, etag: str | None) -> dict[str, str]:
        result = {
            "User-Agent": "Muhammad-Job-Tracker/2.0",
            "Accept": "application/json, text/plain, */*",
        }
        if headers:
            result.update({str(key): str(value) for key, value in headers.items()})
        if etag:
            result["If-None-Match"] = etag
        return result

    def _retry_delay(self, response, attempt: int) -> float:
        retry_after = response.headers.get("Retry-After") if response is not None else None
        if retry_after:
            try:
                return min(float(retry_after), self.retry.backoff_cap)
            except (TypeError, ValueError):
                try:
                    target = parsedate_to_datetime(str(retry_after))
                    if target.tzinfo is None:
                        target = target.replace(tzinfo=timezone.utc)
                    return min(
                        max(0.0, (target - datetime.now(timezone.utc)).total_seconds()),
                        self.retry.backoff_cap,
                    )
                except (TypeError, ValueError, OverflowError):
                    pass
        return min(
            self.retry.backoff_base * (2 ** (attempt - 1)) + self.jitter(),
            self.retry.backoff_cap,
        )

    def _request(
        self,
        method: str,
        url: str,
        *,
        params: dict | None = None,
        payload: dict | None = None,
        headers: dict | None = None,
        etag: str | None = None,
    ):
        parsed = urlsplit(url)
        if parsed.scheme != "https" or not parsed.hostname:
            raise ValueError("official source URL must be absolute HTTPS")
        request_headers = self._headers(headers, etag)
        attempts = self.retry.attempts
        if isinstance(attempts, bool) or attempts < 1:
            raise ValueError("retry attempts must be at least one")
        for attempt in range(1, attempts + 1):
            self.pacer.wait(parsed.hostname, self.host_interval)
            try:
                response = self.session.request(
                    method,
                    url,
                    params=params,
                    json=payload,
                    headers=request_headers,
                    timeout=(self.retry.connect_timeout, self.retry.read_timeout),
                )
            except (requests.Timeout, requests.ConnectionError) as exc:
                if attempt < attempts:
                    self.sleep(self._retry_delay(None, attempt))
                    continue
                raise requests.RequestException(
                    f"{method} {parsed.hostname} failed after {attempts} attempts"
                ) from exc
            status = int(response.status_code)
            if status in _RETRYABLE_STATUSES and attempt < attempts:
                self.sleep(self._retry_delay(response, attempt))
                continue
            if status >= 400:
                raise requests.HTTPError(
                    f"{method} {parsed.hostname} failed with status {status}",
                    response=response,
                )
            return response
        raise AssertionError("unreachable retry loop")

    def _request_json(self, method: str, url: str, **kwargs) -> JsonResponse:
        response = self._request(method, url, **kwargs)
        response_etag = response.headers.get("ETag")
        if response.status_code == 304:
            return JsonResponse(None, 304, response_etag, True)
        try:
            data = response.json()
        except (ValueError, requests.JSONDecodeError) as exc:
            hostname = urlsplit(url).hostname or "unknown-host"
            raise ValueError(f"{method} {hostname} returned invalid JSON") from exc
        return JsonResponse(data, int(response.status_code), response_etag, False)

    def _request_text(self, method: str, url: str, **kwargs) -> TextResponse:
        response = self._request(method, url, **kwargs)
        response_etag = response.headers.get("ETag")
        if response.status_code == 304:
            return TextResponse("", 304, response_etag, True)
        return TextResponse(
            str(response.text), int(response.status_code), response_etag, False
        )
