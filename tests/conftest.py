"""Общие заглушки для тестов: реальные API не вызываются."""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Фиктивный токен: нужен только чтобы проверить, что он нигде не всплывает.
FAKE_TOKEN = "fake-test-token-0000000000"
API = "https://cloud-api.yandex.net/v1/disk"


# Маркер «тела нет / тело не JSON»: json() бросит ValueError, как requests.
NO_BODY = object()


class FakeResponse:
    def __init__(self, status_code=200, json_data=NO_BODY, reason=""):
        self.status_code = status_code
        self._json = json_data
        self.reason = reason

    def json(self):
        if self._json is NO_BODY:
            raise ValueError("Expecting value: line 1 column 1 (char 0)")
        return self._json


class FakeSession:
    """Отдаёт заранее подготовленные ответы и запоминает запросы."""

    def __init__(self, responses=()):
        self.headers = {}
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, timeout=None, **kwargs):
        self.calls.append({"method": method, "url": url,
                           "timeout": timeout, **kwargs})
        if not self.responses:
            raise AssertionError(f"Неожиданный запрос {method} {url}")
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class FakeClock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def make_client(clock):
    from yandex_disk import YandexDiskClient

    def factory(*responses):
        session = FakeSession(responses)
        client = YandexDiskClient(FAKE_TOKEN, session=session,
                                  sleep=clock.sleep, clock=clock)
        return client, session

    return factory
