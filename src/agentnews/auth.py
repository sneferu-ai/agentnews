"""API key validation + in-memory cache (FR-012, FR-016, FR-017, §5.4).

Keys are 32-char hex prefixed ``ak_``; stored as SHA-256 hashes. Valid keys are
cached in memory with a 60-second TTL. Revocation invalidates the revoked key's
cache entry immediately (FR-017); other stale entries expire within 60s.
"""
from __future__ import annotations

import hashlib
import re
import secrets
import sqlite3
import time
from typing import Optional, Tuple

KEY_RE = re.compile(r"^ak_[a-f0-9]{32}$")
CACHE_TTL = 60.0


class KeyCache:
    def __init__(self) -> None:
        self._entries: dict = {}  # key_hash -> (valid: bool, expires_at: float)

    def get(self, key_hash: str) -> Optional[bool]:
        entry = self._entries.get(key_hash)
        if entry is None:
            return None
        valid, expires_at = entry
        if time.monotonic() > expires_at:
            del self._entries[key_hash]
            return None
        return valid

    def set(self, key_hash: str, valid: bool) -> None:
        self._entries[key_hash] = (valid, time.monotonic() + CACHE_TTL)

    def invalidate(self, key_hash: str) -> None:
        self._entries.pop(key_hash, None)

    def clear(self) -> None:
        self._entries.clear()


def generate_key() -> Tuple[str, str, str]:
    """Returns (plaintext_key, key_hash, key_prefix)."""
    raw = secrets.token_hex(16)  # 32 hex chars
    plaintext = f"ak_{raw}"
    key_hash = hashlib.sha256(plaintext.encode("utf-8")).hexdigest()
    return plaintext, key_hash, plaintext[:8]


def hash_key(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


def validate_key(plaintext: str, conn: sqlite3.Connection, cache: KeyCache) -> Tuple[bool, str]:
    """Returns (valid, reason). reason is '' on success or a code string."""
    if not plaintext or not KEY_RE.match(plaintext):
        return False, "unauthorized"
    kh = hash_key(plaintext)
    cached = cache.get(kh)
    if cached is True:
        return True, ""
    if cached is False:
        return False, "key_revoked_or_expired"
    row = conn.execute(
        "SELECT status, expires_at FROM api_keys WHERE key_hash = ?", (kh,)
    ).fetchone()
    if row is None:
        cache.set(kh, False)
        return False, "unauthorized"
    status = row["status"]
    expires_at = row["expires_at"]
    now = _now_iso()
    if status == "revoked":
        cache.set(kh, False)
        return False, "key_revoked_or_expired"
    if expires_at <= now:
        cache.set(kh, False)
        return False, "key_revoked_or_expired"
    cache.set(kh, True)
    return True, ""


def _now_iso() -> str:
    import datetime as _dt

    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
