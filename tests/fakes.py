from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.fetchers.http import JsonResponse, TextResponse


@dataclass(frozen=True, slots=True)
class HttpCall:
    method: str
    url: str
    params: dict | None
    payload: dict | None
    etag: str | None


class FakeHttp:
    def __init__(self, responses: list[Any] | tuple[Any, ...] = ()) -> None:
        self.responses = list(responses)
        self.calls: list[HttpCall] = []

    def queue_json(
        self,
        data: dict | list | None,
        status_code: int = 200,
        etag: str | None = None,
        not_modified: bool = False,
    ) -> None:
        self.responses.append(JsonResponse(data, status_code, etag, not_modified))

    def queue_error(self, error: Exception) -> None:
        self.responses.append(error)

    def queue_text(
        self,
        value: str,
        status_code: int = 200,
        etag: str | None = None,
        not_modified: bool = False,
    ) -> None:
        self.responses.append(TextResponse(value, status_code, etag, not_modified))

    def _next(self) -> JsonResponse | TextResponse:
        if not self.responses:
            raise AssertionError("unexpected HTTP request")
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        if isinstance(item, (JsonResponse, TextResponse)):
            return item
        return JsonResponse(item, 200, None, False)

    def get_json(
        self,
        url: str,
        *,
        params: dict | None = None,
        headers: dict | None = None,
        etag: str | None = None,
    ) -> JsonResponse:
        self.calls.append(HttpCall("GET", url, params, None, etag))
        result = self._next()
        if not isinstance(result, JsonResponse):
            raise AssertionError("expected a JSON response")
        return result

    def get_text(
        self,
        url: str,
        *,
        params: dict | None = None,
        headers: dict | None = None,
        etag: str | None = None,
    ) -> TextResponse:
        self.calls.append(HttpCall("GET_TEXT", url, params, None, etag))
        result = self._next()
        if not isinstance(result, TextResponse):
            raise AssertionError("expected a text response")
        return result

    def post_json(
        self,
        url: str,
        *,
        payload: dict,
        headers: dict | None = None,
    ) -> JsonResponse:
        self.calls.append(HttpCall("POST", url, None, payload, None))
        result = self._next()
        if not isinstance(result, JsonResponse):
            raise AssertionError("expected a JSON response")
        return result


def load_json_fixture(name: str) -> Any:
    fixture_path = Path(__file__).parent / "fixtures" / name
    return json.loads(fixture_path.read_text(encoding="utf-8"))


def load_text_fixture(name: str) -> str:
    fixture_path = Path(__file__).parent / "fixtures" / name
    return fixture_path.read_text(encoding="utf-8")
