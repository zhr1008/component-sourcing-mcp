from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import threading
from collections import OrderedDict
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from typing import Any

from platformdirs import user_cache_dir


class SqliteCache:
    def __init__(self, path: str | Path | None = None, *, memory_size: int = 256):
        self.path = Path(path or Path(user_cache_dir("szlcsc-mcp")) / "cache.sqlite3")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._memory_size = max(0, memory_size)
        self._memory: OrderedDict[str, tuple[Any, int]] = OrderedDict()
        self._lock = threading.RLock()
        self._init_db()

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            with db:
                yield db
        finally:
            # sqlite's own context manager commits, but does not close handles.
            db.close()

    def _init_db(self) -> None:
        with self._connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS http_cache (
                    cache_key TEXT PRIMARY KEY,
                    url TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    fetched_at INTEGER NOT NULL
                )
                """
            )
            db.execute("CREATE INDEX IF NOT EXISTS idx_http_cache_fetched ON http_cache(fetched_at)")

    @staticmethod
    def make_key(method: str, url: str, payload: Any) -> str:
        raw = json.dumps([method.upper(), url, payload], ensure_ascii=False, sort_keys=True, default=str)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def get_entry(self, key: str) -> tuple[Any, int] | None:
        with self._lock:
            if key in self._memory:
                self._memory.move_to_end(key)
                return deepcopy(self._memory[key])
            with self._connect() as db:
                row = db.execute(
                    "SELECT payload, fetched_at FROM http_cache WHERE cache_key = ?", (key,)
                ).fetchone()
            if not row:
                return None
            payload, fetched_at = row
            try:
                entry = (json.loads(payload), int(fetched_at))
            except (ValueError, TypeError):
                return None  # A damaged cache row must not take down sourcing tools.
            self._remember(key, entry)
            return entry

    def _remember(self, key: str, entry: tuple[Any, int]) -> None:
        with self._lock:
            self._memory[key] = deepcopy(entry)
            self._memory.move_to_end(key)
            while len(self._memory) > self._memory_size:
                self._memory.popitem(last=False)

    def get(self, key: str, ttl_seconds: int) -> Any | None:
        entry = self.get_entry(key)
        if not entry:
            return None
        payload, fetched_at = entry
        if int(time.time()) - fetched_at >= ttl_seconds:
            return None
        return payload

    def get_stale(self, key: str, max_age_seconds: int = 7 * 86400) -> Any | None:
        entry = self.get_entry(key)
        if not entry:
            return None
        payload, fetched_at = entry
        if int(time.time()) - fetched_at > max_age_seconds:
            return None
        return payload

    def set(self, key: str, url: str, payload: Any) -> None:
        encoded = json.dumps(payload, ensure_ascii=False, default=str)
        fetched_at = int(time.time())
        with self._lock:
            with self._connect() as db:
                db.execute(
                    "INSERT OR REPLACE INTO http_cache(cache_key,url,payload,fetched_at) VALUES(?,?,?,?)",
                    (key, url, encoded, fetched_at),
                )
            self._remember(key, (payload, fetched_at))

    def status(self) -> dict[str, Any]:
        with self._connect() as db:
            count, newest, oldest = db.execute(
                "SELECT COUNT(*), MAX(fetched_at), MIN(fetched_at) FROM http_cache"
            ).fetchone()
        return {"path": str(self.path), "entries": count, "newest_unix": newest, "oldest_unix": oldest,
                "memory_entries": len(self._memory), "memory_capacity": self._memory_size}

    def prune(self, older_than_seconds: int = 30 * 86400) -> int:
        with self._lock:
            self._memory.clear()
            cutoff = int(time.time()) - older_than_seconds
            with self._connect() as db:
                cur = db.execute("DELETE FROM http_cache WHERE fetched_at < ?", (cutoff,))
                return cur.rowcount

    def clear(self) -> int:
        with self._lock:
            self._memory.clear()
            with self._connect() as db:
                cur = db.execute("DELETE FROM http_cache")
                return cur.rowcount
