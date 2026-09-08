from types import SimpleNamespace

import pytest
import requests

from src.alerts import (TelegramNotifier, TelegramConfigurationError,
                        TelegramTransientError, TelegramPermanentError, TelegramAuthError)


class Clock:
    def __init__(self):
        self.now = 100.0
        self.sleeps = []

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


class Session:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url.rsplit('/', 1)[-1], kwargs))
        value = self.responses.pop(0)
        if isinstance(value, Exception):
            raise value
        status, body = value
        return SimpleNamespace(status_code=status, headers={}, json=lambda: body)


def success(message_id=9):
    return (200, {'ok': True, 'result': {'message_id': message_id}})


def notifier(responses):
    clock = Clock()
    session = Session(responses)
    obj = TelegramNotifier('secret-token', 'private-chat', session=session,
                           sleep=clock.sleep, monotonic=lambda: clock.now, jitter=lambda: 0)
    return obj, session, clock


def test_missing_credentials_are_fatal():
    with pytest.raises(TelegramConfigurationError):
        TelegramNotifier.from_env({'TELEGRAM_CHAT_ID': '123'})


def test_validation_never_sends():
    obj, session, _ = notifier([success(), success()])
    obj.validate_credentials()
    assert [method for method, _ in session.calls] == ['getMe', 'getChat']


def test_429_retry_after():
    obj, session, clock = notifier([(429, {'ok': False, 'parameters': {'retry_after': 4}}), success()])
    assert obj.send_message('hello').message_id == 9
    assert clock.sleeps == [4]
    assert len(session.calls) == 2


@pytest.mark.parametrize('failure', [requests.Timeout('secret-token'), (500, {'ok': False})])
def test_transient_retry(failure):
    obj, session, clock = notifier([failure, success()])
    assert obj.send_message('hello').message_id == 9
    assert clock.sleeps == [2]


def test_three_attempts_and_redaction():
    obj, session, _ = notifier([requests.ConnectionError('private-chat secret-token')] * 3)
    with pytest.raises(TelegramTransientError) as caught:
        obj.send_message('hello')
    assert len(session.calls) == 3
    assert 'secret-token' not in str(caught.value)
    assert 'private-chat' not in str(caught.value)
    assert caught.value.__cause__ is None


@pytest.mark.parametrize('status,error', [(400, TelegramPermanentError), (401, TelegramAuthError),
                                         (403, TelegramAuthError), (200, TelegramPermanentError)])
def test_permanent_response_not_retried(status, error):
    obj, session, _ = notifier([(status, {'ok': False, 'description': 'secret-token'})])
    with pytest.raises(error):
        obj.send_message('hello')
    assert len(session.calls) == 1


def test_success_pacing_and_payload_integrity():
    obj, session, clock = notifier([success(), success(10)])
    payload = 'quote " $HOME `touch nope`\n${{ inputs.value }}'
    obj.send_message(payload)
    obj.send_message('next')
    assert clock.sleeps == [1]
    assert session.calls[0][1]['json']['text'] == payload


def test_missing_receipt_fails_closed():
    obj, _, _ = notifier([(200, {'ok': True, 'result': {}})])
    with pytest.raises(TelegramPermanentError):
        obj.send_message('hello')
