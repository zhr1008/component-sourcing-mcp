"""Bounded retries and shared per-host pacing; never rotate identities or evade challenges."""

from __future__ import annotations

import math
import random
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Callable
from urllib.parse import urljoin, urlparse

import requests

from .errors import SourcingError


@dataclass
class _HostGate:
    lock: threading.Lock = field(default_factory=threading.Lock)
    last_start: float = 0
    blocked_until: float = 0


_HOSTS: dict[str, _HostGate] = {}
_HOSTS_LOCK = threading.Lock()


def retry_after_seconds(value: str | None) -> float | None:
    if not value:
        return None
    try:
        seconds = float(value)
    except ValueError:
        try:
            seconds = (
                parsedate_to_datetime(value) - datetime.now(timezone.utc)
            ).total_seconds()
        except (ValueError, TypeError, OverflowError):
            return None
    return max(0, seconds) if math.isfinite(seconds) else None


class SafeSession(requests.Session):
    """A session owns its cookie jar; one request mutates it at a time.

    Host gates are shared by all clients in this process, including binary downloads.
    A 401/403 is terminal. A 429 opens a cooldown; a long Retry-After is returned
    immediately instead of occupying an MCP worker. Only read-only requests retry.
    """

    def __init__(self, *, delay: float = 0.5, retries: int = 2, timeout: float = 15):
        super().__init__()
        if (
            not math.isfinite(delay)
            or delay < 0
            or not math.isfinite(timeout)
            or timeout <= 0
        ):
            raise ValueError(
                "HTTP timeout must be positive and delay nonnegative and finite"
            )
        if not 0 <= retries <= 5:
            raise ValueError("HTTP retries must be between 0 and 5")
        self.delay, self.retries, self.default_timeout = delay, retries, timeout
        self._request_lock = threading.RLock()

    def request(self, method, url, **kwargs):
        retry_safe = kwargs.pop("retry_safe", False)
        # Redirects must be explicit: requests otherwise fetches an untrusted
        # Location before the downloader can inspect response.url.
        # Session.get/post insert allow_redirects=True before calling request;
        # overriding it here is necessary to make the policy effective.
        kwargs["allow_redirects"] = False
        kwargs.setdefault("timeout", self.default_timeout)
        host = (urlparse(url).hostname or "").lower()
        with _HOSTS_LOCK:
            gate = _HOSTS.setdefault(host, _HostGate())
        attempts = (
            self.retries + 1 if retry_safe or method.upper() in {"GET", "HEAD"} else 1
        )
        with self._request_lock:
            for attempt in range(attempts):
                with gate.lock:
                    now = time.monotonic()
                    if gate.blocked_until > now:
                        raise SourcingError(
                            "Upstream cooldown is active; retry later",
                            code="RATE_LIMITED",
                            retryable=True,
                            retry_after=gate.blocked_until - now,
                        )
                    wait = self.delay - (now - gate.last_start)
                    if wait > 0:
                        time.sleep(wait)
                    gate.last_start = time.monotonic()
                try:
                    response = super().request(method, url, **kwargs)
                except (requests.Timeout, requests.ConnectionError) as exc:
                    if attempt + 1 == attempts:
                        raise SourcingError(
                            "Upstream connection timed out or failed",
                            code="NETWORK_ERROR",
                            retryable=True,
                        ) from exc
                else:
                    status = response.status_code
                    if status in {401, 403}:
                        response.close()
                        with gate.lock:
                            gate.blocked_until = time.monotonic() + 60
                        raise SourcingError(
                            "Upstream requires authorization or blocked this request; no bypass attempted",
                            code="ACCESS_DENIED",
                            retry_after=60,
                        )
                    if status == 429:
                        seconds = retry_after_seconds(
                            response.headers.get("Retry-After")
                        )
                        seconds = max(1, seconds if seconds is not None else 30)
                        response.close()
                        with gate.lock:
                            gate.blocked_until = time.monotonic() + seconds
                        raise SourcingError(
                            "Upstream rate limit reached",
                            code="RATE_LIMITED",
                            retryable=True,
                            retry_after=seconds,
                        )
                    if status not in {408, 425, 500, 502, 503, 504}:
                        return response
                    if attempt + 1 == attempts:
                        return response
                    response.close()
                time.sleep(min(2**attempt + random.uniform(0, 0.25), 5))
        raise AssertionError("unreachable retry state")


def safe_get(
    session: requests.Session,
    url: str,
    *,
    validate: Callable[[str], None],
    timeout: float,
    stream: bool = False,
) -> requests.Response:
    """Validate every redirect *before* making the next request."""
    for _ in range(6):
        validate(url)
        response = session.get(
            url, timeout=timeout, stream=stream, allow_redirects=False
        )
        if response.status_code not in {301, 302, 303, 307, 308}:
            return response
        location = response.headers.get("Location")
        response.close()
        if not location:
            raise SourcingError(
                "Upstream redirect has no Location", code="INVALID_RESPONSE"
            )
        url = urljoin(url, location)
    raise SourcingError("Too many upstream redirects", code="INVALID_RESPONSE")
