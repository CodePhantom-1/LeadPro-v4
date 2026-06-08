"""
cache.py — SQLite-backed AI response cache with TTL.

Storage: one row per prompt (SHA256 key). Stores JSON-encoded value and a
per-row TTL. Uses the shared connection pool from database.py to avoid
creating separate SQLite connections that compete with data tables.
"""
import hashlib
import json
import time
from database import get_conn


def _init_cache():
    with get_conn() as c:
        c.execute(
            """CREATE TABLE IF NOT EXISTS ai_cache (
                key          TEXT PRIMARY KEY,
                value        TEXT NOT NULL,
                created_at   REAL NOT NULL,
                ttl_seconds  INTEGER DEFAULT 86400
            )"""
        )


def _key(prompt: str) -> str:
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


def cache_get(prompt: str):
    """Return the cached value (decoded from JSON) or None if missing/expired."""
    key = _key(prompt)
    try:
        with get_conn() as c:
            row = c.execute(
                "SELECT value, created_at, ttl_seconds FROM ai_cache WHERE key=?",
                (key,),
            ).fetchone()
            if not row:
                return None
            value, created_at, ttl_seconds = row
            if ttl_seconds is not None and (time.time() - created_at) >= ttl_seconds:
                c.execute("DELETE FROM ai_cache WHERE key=?", (key,))
                return None
            try:
                return json.loads(value)
            except (json.JSONDecodeError, TypeError):
                return None
    except Exception:
        return None


def cache_set(prompt: str, value, ttl: int = 86400):
    """Store a value under the prompt key. Value is JSON-encoded."""
    key = _key(prompt)
    try:
        payload = json.dumps(value)
    except (TypeError, ValueError):
        return
    try:
        with get_conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO ai_cache (key, value, created_at, ttl_seconds) VALUES (?,?,?,?)",
                (key, payload, time.time(), int(ttl)),
            )
    except Exception:
        pass


# Ensure cache table exists on import
_init_cache()

def cache_purge_expired() -> int:
    """Delete expired rows. Returns number of rows removed."""
    try:
        with get_conn() as c:
            cur = c.execute(
                "DELETE FROM ai_cache WHERE (? - created_at) >= ttl_seconds",
                (time.time(),),
            )
            return cur.rowcount or 0
    except Exception:
        return 0
