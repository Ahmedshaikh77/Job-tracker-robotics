"""Bounded Telegram transport. Never expose credentials in errors or logs."""
from __future__ import annotations

import os
import random
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import requests


class TelegramConfigurationError(ValueError):
    pass


class TelegramTransientError(RuntimeError):
    pass


class TelegramPermanentError(RuntimeError):
    pass


class TelegramAuthError(TelegramPermanentError):
    pass


@dataclass(frozen=True, slots=True)
class TelegramReceipt:
    message_id: int
    delivered_at: str


class TelegramNotifier:
    def __init__(self, bot_token, chat_id, *, policy=None, session=None,
                 sleep=time.sleep, monotonic=time.monotonic, jitter=random.random,
                 now=lambda: datetime.now(timezone.utc)):
        if not bot_token or not chat_id:
            raise TelegramConfigurationError('Telegram credentials are missing')
        if policy is None:
            from .config import TelegramPolicy
            policy = TelegramPolicy()
        self._token = bot_token
        self._chat_id = chat_id
        self.policy = policy
        self.session = session or requests.Session()
        self.sleep = sleep
        self.monotonic = monotonic
        self.jitter = jitter
        self.now = now
        self._last_send = None

    @classmethod
    def from_env(cls, environ=None, policy=None):
        environ = os.environ if environ is None else environ
        return cls(environ.get('TELEGRAM_BOT_TOKEN', ''), environ.get('TELEGRAM_CHAT_ID', ''), policy=policy)

    def _retry_delay(self, response, body, attempt):
        raw = (body.get('parameters') or {}).get('retry_after') if isinstance(body, dict) else None
        raw = raw if raw is not None else (response.headers.get('Retry-After') if response is not None else None)
        if raw is not None:
            try:
                return max(0.0, float(raw))
            except (ValueError, TypeError):
                try:
                    return max(0.0, (parsedate_to_datetime(raw) - self.now()).total_seconds())
                except (ValueError, TypeError, OverflowError):
                    pass
        return min(self.policy.backoff_cap_seconds,
                   self.policy.backoff_base_seconds * 2 ** attempt + self.jitter())

    def _request(self, method, payload):
        for attempt in range(self.policy.max_attempts):
            response = None
            body = None
            transient = False
            try:
                response = self.session.post(
                    f'https://api.telegram.org/bot{self._token}/{method}',
                    json=payload, timeout=self.policy.timeout_seconds)
                try:
                    body = response.json()
                except (ValueError, TypeError):
                    body = None
                status = response.status_code
                if status in (401, 403):
                    raise TelegramAuthError('Telegram authorization failed')
                transient = status in (429, 500, 502, 503, 504)
                if not transient:
                    if status != 200 or not isinstance(body, dict) or body.get('ok') is not True:
                        raise TelegramPermanentError('Telegram rejected the request')
                    if not isinstance(body.get('result'), dict):
                        raise TelegramPermanentError('Telegram returned an invalid result')
                    return body['result']
            except (requests.Timeout, requests.ConnectionError):
                transient = True
            except requests.RequestException:
                raise TelegramPermanentError('Telegram request failed') from None
            if transient:
                if attempt + 1 == self.policy.max_attempts:
                    raise TelegramTransientError('Telegram unavailable after bounded retries') from None
                self.sleep(self._retry_delay(response, body, attempt))
        raise TelegramTransientError('Telegram retry limit exhausted')

    def validate_credentials(self):
        self._request('getMe', {})
        self._request('getChat', {'chat_id': self._chat_id})

    def send_message(self, text):
        if not isinstance(text, str) or not text:
            raise TelegramPermanentError('Telegram message is empty')
        if self._last_send is not None:
            delay = self.policy.minimum_send_interval_seconds - (self.monotonic() - self._last_send)
            if delay > 0:
                self.sleep(delay)
        result = self._request('sendMessage', {'chat_id': self._chat_id, 'text': text,
                              'parse_mode': 'HTML', 'disable_web_page_preview': True})
        message_id = result.get('message_id')
        if not isinstance(message_id, int) or isinstance(message_id, bool):
            raise TelegramPermanentError('Telegram delivery receipt is invalid')
        self._last_send = self.monotonic()
        return TelegramReceipt(message_id, self.now().astimezone(timezone.utc).isoformat().replace('+00:00', 'Z'))
