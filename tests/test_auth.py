"""Tests for API key auth and cache (FR-012, FR-016, FR-017)."""
import hashlib
import time

from agentnews import auth, storage
from agentnews.config import load_config


def test_generate_key_format():
    plaintext, key_hash, prefix = auth.generate_key()
    assert plaintext.startswith("ak_")
    assert len(plaintext) == 35
    assert auth.KEY_RE.match(plaintext)
    assert key_hash == auth.hash_key(plaintext)
    assert prefix == plaintext[:8]


def test_hash_key_is_sha256_hex():
    plaintext = "ak_" + "0" * 32
    assert auth.hash_key(plaintext) == hashlib.sha256(plaintext.encode()).hexdigest()


def test_cache_ttl_and_invalidation():
    cache = auth.KeyCache()
    cache.set("kh1", True)
    assert cache.get("kh1") is True
    cache.invalidate("kh1")
    assert cache.get("kh1") is None

    cache.set("kh2", True)
    # Force entry to appear expired
    cache._entries["kh2"] = (True, time.monotonic() - 1)
    assert cache.get("kh2") is None


def test_validate_key_active(conn):
    config = load_config()
    plaintext, kh, _ = auth.generate_key()
    storage.insert_api_key(conn, plaintext, "test", config, days=1)
    cache = auth.KeyCache()
    valid, reason = auth.validate_key(plaintext, conn, cache)
    assert valid
    assert reason == ""
    assert cache.get(kh) is True


def test_validate_key_revoked(conn):
    config = load_config()
    plaintext, kh, _ = auth.generate_key()
    storage.insert_api_key(conn, plaintext, "test", config, days=1)
    storage.revoke_api_key(conn, plaintext)
    cache = auth.KeyCache()
    valid, reason = auth.validate_key(plaintext, conn, cache)
    assert not valid
    assert reason == "key_revoked_or_expired"
    assert cache.get(kh) is False


def test_validate_key_expired(conn):
    config = load_config()
    plaintext, kh, _ = auth.generate_key()
    storage.insert_api_key(conn, plaintext, "test", config, days=1)
    conn.execute(
        "UPDATE api_keys SET expires_at='2020-01-01T00:00:00Z' WHERE key_hash=?",
        (kh,),
    )
    conn.commit()
    cache = auth.KeyCache()
    valid, reason = auth.validate_key(plaintext, conn, cache)
    assert not valid
    assert reason == "key_revoked_or_expired"


def test_validate_key_invalid_format():
    cache = auth.KeyCache()
    valid, reason = auth.validate_key("not-a-key", None, cache)
    assert not valid
    assert reason == "unauthorized"


def test_validate_key_unknown(conn):
    # Any well-formed key that is not in the DB is unauthorized.
    plaintext, kh, _ = auth.generate_key()
    cache = auth.KeyCache()
    valid, reason = auth.validate_key(plaintext, conn, cache)
    assert not valid
    assert reason == "unauthorized"
    assert cache.get(kh) is False


def test_validate_key_cached_positive(conn):
    config = load_config()
    plaintext, kh, _ = auth.generate_key()
    storage.insert_api_key(conn, plaintext, "test", config, days=1)
    cache = auth.KeyCache()
    cache.set(kh, True)
    valid, reason = auth.validate_key(plaintext, conn, cache)
    assert valid
    assert reason == ""


def test_validate_key_cached_negative(conn):
    config = load_config()
    plaintext, kh, _ = auth.generate_key()
    storage.insert_api_key(conn, plaintext, "test", config, days=1)
    cache = auth.KeyCache()
    cache.set(kh, False)
    valid, reason = auth.validate_key(plaintext, conn, cache)
    assert not valid
    assert reason == "key_revoked_or_expired"
