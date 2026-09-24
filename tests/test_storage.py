"""Tests for storage operations (FR-004, FR-006, FR-018, FR-019, FR-021,
FR-022, FR-030, FR-043)."""
import json
import sqlite3
from unittest.mock import patch

import pytest

from agentnews import ingest, storage
from agentnews.config import load_config


def _parse_good(good_run_dir):
    config = load_config()
    return ingest.parse_run(good_run_dir, config)


def test_insert_and_get(conn, good_run_dir):
    config = load_config()
    parsed = _parse_good(good_run_dir)
    pid = storage.insert_package(conn, parsed, config)
    pkg = storage.get_package(conn, pid)
    assert pkg["question"] == parsed.question
    assert pkg["quality_score"] == pytest.approx(78.5)
    assert pkg["status"] == "imported"
    assert pkg["content_hash"] == parsed.content_hash
    cits = storage.get_citations(conn, pid)
    assert len(cits) == 5
    assert cits[0]["ordinal"] == 1


def test_insert_duplicate_run_rejects(conn, good_run_dir):
    config = load_config()
    parsed = _parse_good(good_run_dir)
    storage.insert_package(conn, parsed, config)
    with pytest.raises(storage.DuplicateRunError):
        storage.insert_package(conn, parsed, config)


def test_count_packages(conn, good_run_dir, tmp_path):
    """count_packages returns total matching rows regardless of limit/offset."""
    import shutil
    config = load_config()
    parsed1 = _parse_good(good_run_dir)
    pid1 = storage.insert_package(conn, parsed1, config)
    storage.transition_to_published(conn, pid1, config)
    run2 = tmp_path / "run-good-2"
    shutil.copytree(good_run_dir, run2)
    parsed2 = ingest.parse_run(run2, config)
    pid2 = storage.insert_package(conn, parsed2, config)
    storage.transition_to_published(conn, pid2, config)
    assert storage.count_packages(conn, status="published") == 2
    assert len(storage.list_packages(conn, limit=1, offset=0, status="published")) == 1


def test_slug_is_kebab(conn, good_run_dir):
    config = load_config()
    parsed = _parse_good(good_run_dir)
    pid = storage.insert_package(conn, parsed, config)
    pkg = storage.get_package(conn, pid)
    slug = pkg["slug"]
    assert "-" in slug or slug.isalpha()
    assert " " not in slug
    assert all(c.isalnum() or c == "-" for c in slug)


def test_package_id_format(conn, good_run_dir):
    """FR-004: package IDs are pkg-YYYYMMDD-xxxxxx."""
    import re
    config = load_config()
    parsed = _parse_good(good_run_dir)
    pid = storage.insert_package(conn, parsed, config)
    assert re.fullmatch(r"pkg-\d{8}-[a-f0-9]{6}", pid)


def test_slug_equals_package_id(conn, good_run_dir):
    """FR-006: slug is identical to the package ID."""
    config = load_config()
    parsed = _parse_good(good_run_dir)
    pid = storage.insert_package(conn, parsed, config)
    pkg = storage.get_package(conn, pid)
    assert pkg["slug"] == pid


def test_slug_uniqueness(conn, good_run_dir, tmp_path):
    import shutil
    config = load_config()
    parsed = _parse_good(good_run_dir)
    pid1 = storage.insert_package(conn, parsed, config)
    # second run with same question but different run_id
    run2 = tmp_path / "run-good-2"
    shutil.copytree(good_run_dir, run2)
    parsed2 = ingest.parse_run(run2, config)
    pid2 = storage.insert_package(conn, parsed2, config)
    p1 = storage.get_package(conn, pid1)
    p2 = storage.get_package(conn, pid2)
    assert p1["slug"] != p2["slug"]


def test_transition_to_published_sets_latency(conn, good_run_dir):
    config = load_config()
    parsed = _parse_good(good_run_dir)
    pid = storage.insert_package(conn, parsed, config)
    storage.transition_to_published(conn, pid, config)
    pkg = storage.get_package(conn, pid)
    assert pkg["status"] == "published"
    assert pkg["published_at"] is not None
    assert pkg["latency_hours"] is not None
    assert "hours" in pkg["latency_disclosure"].lower()


def test_withhold_and_republish(conn, good_run_dir):
    config = load_config()
    parsed = _parse_good(good_run_dir)
    pid = storage.insert_package(conn, parsed, config)
    storage.transition_to_published(conn, pid, config)
    pkg = storage.get_package(conn, pid)
    original_pub_at = pkg["published_at"]
    storage.withhold_package(conn, pid)
    pkg = storage.get_package(conn, pid)
    assert pkg["status"] == "withheld"
    assert pkg["withheld_at"] is not None
    storage.republish_package(conn, pid, config)
    pkg = storage.get_package(conn, pid)
    assert pkg["status"] == "published"
    # FR-021: republish must refresh published_at and latency fields.
    assert pkg["withheld_at"] is None
    assert pkg["published_at"] is not None
    assert pkg["latency_hours"] is not None
    assert pkg["latency_disclosure"]


def test_withhold_only_from_published(conn, good_run_dir):
    config = load_config()
    parsed = _parse_good(good_run_dir)
    pid = storage.insert_package(conn, parsed, config)
    with pytest.raises(ValueError):
        storage.withhold_package(conn, pid)


def test_single_sample_replaces_existing(conn, good_run_dir, tmp_path):
    """FR-022: mark_sample clears all other samples first, then sets the target."""
    import shutil
    config = load_config()
    parsed = _parse_good(good_run_dir)
    pid1 = storage.insert_package(conn, parsed, config, is_sample=True)
    run2 = tmp_path / "run-good-2"
    shutil.copytree(good_run_dir, run2)
    parsed2 = ingest.parse_run(run2, config)
    pid2 = storage.insert_package(conn, parsed2, config)
    # marking a second sample replaces the first (clears all, then sets target)
    storage.mark_sample(conn, pid2)
    pkg1 = storage.get_package(conn, pid1)
    pkg2 = storage.get_package(conn, pid2)
    assert pkg1["is_sample"] is False
    assert pkg2["is_sample"] is True


def test_recompute_content_hash_matches(conn, good_run_dir):
    config = load_config()
    parsed = _parse_good(good_run_dir)
    pid = storage.insert_package(conn, parsed, config)
    result = storage.recompute_content_hash(conn, pid)
    assert result["match"] is True
    assert result["content_hash_stored"] == result["content_hash_recomputed"]


def test_recompute_detects_tamper(conn, good_run_dir):
    config = load_config()
    parsed = _parse_good(good_run_dir)
    pid = storage.insert_package(conn, parsed, config)
    conn.execute("UPDATE packages SET findings='tampered findings' WHERE id=?", (pid,))
    conn.commit()
    result = storage.recompute_content_hash(conn, pid)
    assert result["match"] is False


def test_api_key_create_validate_revoke(conn):
    from agentnews import auth
    config = load_config()
    plaintext, kh, prefix = auth.generate_key()
    assert plaintext.startswith("ak_")
    rowid, expires_at = storage.insert_api_key(conn, plaintext, "test", config)
    assert rowid > 0
    assert expires_at.endswith("Z")
    valid, reason = auth.validate_key(plaintext, conn, auth.KeyCache())
    assert valid and reason == ""
    storage.revoke_api_key(conn, str(rowid))
    # cache must be invalidated by caller; fresh cache sees revoked
    cache = auth.KeyCache()
    valid, reason = auth.validate_key(plaintext, conn, cache)
    assert not valid
    assert "revoked" in reason


def test_api_key_invalid_format_rejected(conn):
    from agentnews import auth
    cache = auth.KeyCache()
    valid, reason = auth.validate_key("not-a-key", conn, cache)
    assert not valid
    valid, reason = auth.validate_key("ak_short", conn, cache)
    assert not valid


def test_list_packages_published_only(conn, good_run_dir, tmp_path):
    import shutil
    config = load_config()
    parsed = _parse_good(good_run_dir)
    pid = storage.insert_package(conn, parsed, config)
    # not published yet -> not in published list
    assert storage.list_packages(conn, status="published") == []
    storage.transition_to_published(conn, pid, config)
    pkgs = storage.list_packages(conn, status="published")
    assert len(pkgs) == 1
    assert pkgs[0]["id"] == pid


def test_get_sample(conn, good_run_dir):
    config = load_config()
    parsed = _parse_good(good_run_dir)
    pid = storage.insert_package(conn, parsed, config, is_sample=True)
    s = storage.get_sample(conn)
    assert s is not None
    assert s["id"] == pid


def test_id_collision_retries_then_succeeds(conn, good_run_dir, tmp_path, monkeypatch):
    """FR-004/AC-033: ID collision retries up to 3 times, then succeeds."""
    import shutil
    config = load_config()
    parsed1 = _parse_good(good_run_dir)
    ids = ["pkg-20260817-aaaaaa", "pkg-20260817-aaaaaa", "pkg-20260817-bbbbbb"]
    monkeypatch.setattr("agentnews.storage.generate_package_id", lambda: ids.pop(0))
    pid1 = storage.insert_package(conn, parsed1, config)
    assert pid1 == "pkg-20260817-aaaaaa"
    run2 = tmp_path / "run-good-2"
    shutil.copytree(good_run_dir, run2)
    parsed2 = ingest.parse_run(run2, config)
    pid2 = storage.insert_package(conn, parsed2, config)
    assert pid2 == "pkg-20260817-bbbbbb"
    assert len(ids) == 0


def test_id_collision_exhausted_raises(conn, good_run_dir, tmp_path, monkeypatch):
    """FR-004/AC-033: after 3 ID collisions, IdCollisionError is raised."""
    import shutil
    config = load_config()
    parsed1 = _parse_good(good_run_dir)
    monkeypatch.setattr("agentnews.storage.generate_package_id", lambda: "pkg-20260817-aaaaaa")
    storage.insert_package(conn, parsed1, config)
    run2 = tmp_path / "run-good-2"
    shutil.copytree(good_run_dir, run2)
    parsed2 = ingest.parse_run(run2, config)
    monkeypatch.setattr("agentnews.storage.generate_package_id", lambda: "pkg-20260817-aaaaaa")
    with pytest.raises(storage.IdCollisionError):
        storage.insert_package(conn, parsed2, config)


def test_mark_sample_race_raises_sample_race(conn, good_run_dir):
    """FR-022: concurrent sample set raises SampleRaceError.

    SQLite raises IntegrityError when the UPDATE statement executes (not on
    commit), so the mock must target the is_sample=1 UPDATE.
    """
    config = load_config()
    parsed = _parse_good(good_run_dir)
    pid = storage.insert_package(conn, parsed, config)
    exc = sqlite3.IntegrityError("UNIQUE constraint failed: packages.is_sample")

    class FailingExecuteConn:
        def __init__(self, real):
            self._real = real

        def __getattr__(self, name):
            return getattr(self._real, name)

        def execute(self, sql, params=()):
            if "is_sample=1" in sql and "WHERE id=?" in sql:
                raise exc
            return self._real.execute(sql, params)

    with pytest.raises(storage.SampleRaceError):
        storage.mark_sample(FailingExecuteConn(conn), pid)
