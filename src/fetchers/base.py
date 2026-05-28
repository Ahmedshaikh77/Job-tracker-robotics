"""Base fetcher class + registry."""
from __future__ import annotations
import logging
from abc import ABC, abstractmethod
from typing import ClassVar
import requests
from ..models import Job

log = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


class Fetcher(ABC):
    """Base fetcher. Each company in config.yaml has a `fetcher` field
    mapping to one of these subclasses' `name`."""

    name: ClassVar[str] = ""

    def __init__(self, timeout: int = 15):
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": USER_AGENT,
            "Accept": "application/json, text/plain, */*",
        })

    @abstractmethod
    def fetch(self, company: dict) -> list[Job]:
        """Fetch all jobs for one company config entry."""
        ...

    def _get_json(self, url: str, **kwargs) -> dict | list:
        r = self.session.get(url, timeout=self.timeout, **kwargs)
        r.raise_for_status()
        return r.json()

    def _post_json(self, url: str, json: dict, **kwargs) -> dict | list:
        r = self.session.post(url, json=json, timeout=self.timeout, **kwargs)
        r.raise_for_status()
        return r.json()


_REGISTRY: dict[str, type[Fetcher]] = {}


def register(cls: type[Fetcher]) -> type[Fetcher]:
    if not cls.name:
        raise ValueError(f"{cls} missing 'name' class attribute")
    _REGISTRY[cls.name] = cls
    return cls


def get_fetcher(name: str) -> Fetcher:
    if name not in _REGISTRY:
        raise KeyError(f"No fetcher named '{name}'. Available: {sorted(_REGISTRY)}")
    return _REGISTRY[name]()


def available() -> list[str]:
    return sorted(_REGISTRY)
