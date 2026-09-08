from __future__ import annotations

from unittest.mock import Mock

import pytest
import requests

from src.fetchers.http import HostPacer, HttpClient, RetryPolicy


def response(status: int, payload=None, headers=None, text: str = ""):
    item = Mock()
    item.status_code = status
    item.headers = headers or {}
    item.text = text
    item.json.return_value = payload
    item.raise_for_status.side_effect = (
        requests.HTTPError("upstream request failed", response=item)
        if status >= 400
        else None
    )
    return item


def test_get_retries_429_using_retry_after_then_succeeds():
    session = Mock()
    session.request.side_effect = [
        response(429, headers={"Retry-After": "3"}),
        response(200, {"jobs": []}, {"ETag": '"abc"'}),
    ]
    sleeps: list[float] = []
    client = HttpClient(
        session=session,
        retry=RetryPolicy(attempts=3, backoff_base=2, backoff_cap=60),
        pacer=HostPacer(monotonic=lambda: 100.0, sleep=lambda _: None),
        sleep=sleeps.append,
        jitter=lambda: 0,
    )

    result = client.get_json("https://example.test/jobs")

    assert result.data == {"jobs": []}
    assert result.etag == '"abc"'
    assert sleeps == [3]
    assert session.request.call_count == 2


def test_get_returns_not_modified_without_decoding_json():
    session = Mock()
    session.request.return_value = response(304, headers={"ETag": '"abc"'})
    client = HttpClient(
        session=session,
        pacer=HostPacer(monotonic=lambda: 100.0, sleep=lambda _: None),
        sleep=lambda _: None,
        jitter=lambda: 0,
    )

    result = client.get_json("https://example.test/jobs", etag='"abc"')

    assert result.not_modified is True
    assert result.data is None
    session.request.return_value.json.assert_not_called()
    assert session.request.call_args.kwargs["headers"]["If-None-Match"] == '"abc"'


def test_get_text_uses_the_same_status_and_etag_contract():
    session = Mock()
    session.request.return_value = response(
        200, headers={"ETag": '"page"'}, text="<html>ok</html>"
    )
    client = HttpClient(
        session=session,
        pacer=HostPacer(monotonic=lambda: 100.0, sleep=lambda _: None),
    )

    result = client.get_text("https://example.test/jobs/123")

    assert result.text == "<html>ok</html>"
    assert result.etag == '"page"'


def test_error_text_does_not_include_token_bearing_url_or_body():
    session = Mock()
    session.request.return_value = response(401, text="private response SECRET")
    client = HttpClient(
        session=session,
        pacer=HostPacer(monotonic=lambda: 100.0, sleep=lambda _: None),
        sleep=lambda _: None,
        jitter=lambda: 0,
    )

    with pytest.raises(requests.HTTPError) as captured:
        client.get_json("https://example.test/botSECRET/getMe")

    assert "SECRET" not in str(captured.value)
    assert "example.test" in str(captured.value)
    assert "401" in str(captured.value)


def test_nonretryable_403_is_attempted_once():
    session = Mock()
    session.request.return_value = response(403)
    client = HttpClient(
        session=session,
        retry=RetryPolicy(attempts=3),
        pacer=HostPacer(monotonic=lambda: 100.0, sleep=lambda _: None),
        sleep=lambda _: None,
    )

    with pytest.raises(requests.HTTPError):
        client.get_json("https://example.test/jobs")

    assert session.request.call_count == 1


def test_host_pacing_is_shared_across_clients():
    current = [10.0]
    sleeps: list[float] = []

    def monotonic() -> float:
        return current[0]

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        current[0] += seconds

    pacer = HostPacer(monotonic=monotonic, sleep=sleep)
    first_session = Mock()
    first_session.request.return_value = response(200, {})
    second_session = Mock()
    second_session.request.return_value = response(200, {})
    policy = RetryPolicy(per_host_pacing=0.25)
    first = HttpClient(session=first_session, retry=policy, pacer=pacer)
    second = HttpClient(session=second_session, retry=policy, pacer=pacer)

    first.get_json("https://example.test/one")
    second.get_json("https://example.test/two")

    assert sleeps == [0.25]
