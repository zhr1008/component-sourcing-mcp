"""Credential-safe transport and persistent per-account daily request budgets."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import logging
import threading
import time
from decimal import Decimal
from urllib.parse import quote, quote_plus

import requests

from .cache import SqliteCache
from .errors import SourcingError
from .http import SafeSession


def _invalid_json_constant(_value):
    raise ValueError("Nonfinite JSON number")


class SecretRedactor(logging.Filter):
    def __init__(self, *secrets: str):
        super().__init__()
        self._secrets: set[str] = set()
        self._lock = threading.Lock()
        for secret in secrets:
            self.add(secret)

    def add(self, secret: str):
        if secret:
            with self._lock:
                self._secrets.update(
                    (secret, quote(secret, safe=""), quote_plus(secret))
                )

    def redact(self, value):
        if isinstance(value, str):
            with self._lock:
                for secret in sorted(self._secrets, key=len, reverse=True):
                    value = value.replace(secret, "[REDACTED]")
            return value
        if isinstance(value, list):
            return [self.redact(v) for v in value]
        if isinstance(value, dict):
            return {self.redact(k): self.redact(v) for k, v in value.items()}
        return value

    def filter(self, record):
        if not self._secrets:
            return True
        record.msg = self.redact(record.getMessage())
        record.args = ()
        # Transport tracebacks may carry PreparedRequest URLs/headers.
        record.exc_info = record.exc_text = None
        return True


class DailyBudget:
    def __init__(self, cache: SqliteCache, provider: str, scope: str, limit: int):
        self.cache, self.provider, self.scope, self.limit = (
            cache,
            provider,
            scope,
            limit,
        )
        with cache._connect() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS supplier_budget (provider TEXT, scope TEXT, day TEXT, used INTEGER, PRIMARY KEY(provider,scope,day))"
            )

    def consume(self):
        now = datetime.now(timezone.utc)
        day = now.date().isoformat()
        with self.cache._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT used FROM supplier_budget WHERE provider=? AND scope=? AND day=?",
                (self.provider, self.scope, day),
            ).fetchone()
            if row and row[0] >= self.limit:
                tomorrow = (now + timedelta(days=1)).replace(
                    hour=0, minute=0, second=0, microsecond=0
                )
                raise SourcingError(
                    "Local supplier daily request budget exhausted",
                    code="RATE_LIMITED",
                    retryable=True,
                    retry_after=(tomorrow - now).total_seconds(),
                )
            db.execute(
                "INSERT INTO supplier_budget VALUES(?,?,?,1) ON CONFLICT(provider,scope,day) DO UPDATE SET used=used+1",
                (self.provider, self.scope, day),
            )
            db.execute(
                "DELETE FROM supplier_budget WHERE day < ?",
                ((now - timedelta(days=7)).date().isoformat(),),
            )


class BudgetSession(SafeSession):
    def __init__(self, budget: DailyBudget, **kwargs):
        super().__init__(**kwargs)
        self.budget = budget
        self.trust_env = False  # Do not import unrelated netrc credentials/proxies.

    def send(self, request, **kwargs):
        self.budget.consume()  # Counts every actual retry, not just logical calls.
        return super().send(request, **kwargs)


class SupplierHTTP:
    def __init__(
        self,
        cache: SqliteCache,
        provider: str,
        credential: str,
        *,
        secrets=(),
        delay=2.1,
        daily_limit=1000,
    ):
        self.cache, self.provider = cache, provider
        self.scope = hmac.new(
            (credential or "unconfigured").encode(),
            b"component-sourcing-account",
            hashlib.sha256,
        ).hexdigest()
        self.redactor = SecretRedactor(
            credential if credential.strip("\0") else "", *secrets
        )
        self.session = BudgetSession(
            DailyBudget(cache, provider, self.scope, daily_limit),
            delay=delay,
            timeout=15,
            retries=2,
        )
        self.session.headers.update(
            {
                "Accept": "application/json",
                "User-Agent": "component-sourcing-mcp (read-only supplier integration)",
            }
        )
        self.loggers = [
            logging.getLogger(n)
            for n in (
                "urllib3.connectionpool",
                "requests.packages.urllib3.connectionpool",
            )
        ]
        for logger in self.loggers:
            logger.addFilter(self.redactor)
        self.lock = threading.RLock()

    def close(self):
        self.session.close()
        for logger in self.loggers:
            logger.removeFilter(self.redactor)

    def request(self, method, url, *, retry_safe=False, redact_response=True, **kwargs):
        try:
            with self.session.request(
                method, url, retry_safe=retry_safe, stream=True, **kwargs
            ) as response:
                response.raise_for_status()
                if 300 <= response.status_code < 400:
                    raise SourcingError(
                        "Supplier API redirected unexpectedly", code="INVALID_RESPONSE"
                    )
                chunks, size, started = [], 0, time.monotonic()
                for chunk in response.iter_content(65536):
                    size += len(chunk)
                    if size > 8 * 1024 * 1024 or time.monotonic() - started > 30:
                        raise SourcingError(
                            "Supplier response exceeds the transfer limit",
                            code="INVALID_RESPONSE",
                        )
                    chunks.append(chunk)
                payload = json.loads(
                    b"".join(chunks),
                    parse_float=Decimal,
                    parse_constant=_invalid_json_constant,
                )
            if not isinstance(payload, dict):
                raise SourcingError(
                    "Supplier returned an invalid response object",
                    code="INVALID_RESPONSE",
                )
            # OAuth responses are consumed only inside the token manager. An
            # issuer may return an existing token on renewal, which must remain
            # usable even when it is already registered with the log redactor.
            return self.redactor.redact(payload) if redact_response else payload
        except SourcingError as error:
            # Discard sensitive transport messages and their exception chains.
            raise SourcingError(
                "Supplier request unavailable; inspect its status code and retry policy",
                code=error.code,
                retryable=error.retryable,
                retry_after=error.retry_after,
            ) from None
        except requests.HTTPError as error:
            status = error.response.status_code if error.response is not None else None
            code = "PART_NOT_FOUND" if status == 404 else "UPSTREAM_REJECTED"
            raise SourcingError(
                "Supplier API returned an unsuccessful status", code=code
            ) from None
        except (requests.RequestException, ValueError, TypeError):
            raise SourcingError(
                "Supplier response unavailable or invalid", code="INVALID_RESPONSE"
            ) from None

    def cache_key(self, url: str, request: dict):
        return self.cache.make_key(
            "SUPPLIER", url, {"account": self.scope, "request": request}
        )

    def read(self, key: str, refresh: bool, ttl: int = 120):
        entry = self.cache.get_entry(key)
        if not refresh and entry is not None and time.time() - entry[1] < ttl:
            # A previous cache file is not a trusted source of credential bytes.
            return self.redactor.redact(entry[0]), entry[1]
        return None

    def write(self, key, url, records, total):
        payload = self.redactor.redact({"records": records, "total": total})
        # Store cleaned models only; never store a PreparedRequest or raw API JSON.
        json.dumps(payload, allow_nan=False)
        self.cache.set(key, url, payload)
