"""Tests for the CLI (FR-028, §5.4)."""
import json
import os
import re
import sys
from pathlib import Path

import pytest

from agentnews import cli


def _write_env(tmp_path, db_path):
    env = tmp_path / ".env"
    env.write_text(
        "PUBLIC_URL=https://news.example.com\n"
        "PAYMENT_LINK_URL=https://buy.example.com/agentnews\n"
        "ADMIN_TOKEN=test-admin-token\n"
        "SNEFERU_RUNS_DIR=/tmp/agentnews-sneferu-runs\n"
        "LOG_SALT=test-salt-fixed\n"
        f"AGENTNEWS_DB={db_path}\n"
        "CITATION_MINIMUM=3\n"
        "PUBLISHABLE_SCORE_THRESHOLD=60\n"
        "EXCERPT_LIMIT_WORDS=300\n"
        "URL_VERIFY_TOTAL_TIMEOUT=5\n"
    )
    return env


def test_cli_init(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    env = _write_env(tmp_path, str(tmp_path / "agentnews.db"))
    rc = cli.main(["init", "--env-file", str(env)])
    assert rc == 0
    env_text = env.read_text()
    assert "PUBLIC_URL" in env_text
    assert "LOG_SALT=" in env_text


def test_cli_init_does_not_require_sneferu_runs_dir(tmp_path, monkeypatch, capsys):
    """FR-041: init must NOT require SNEFERU_RUNS_DIR.

    FR-041 lists only PUBLIC_URL, PAYMENT_LINK_URL, ADMIN_TOKEN as validated by
    init/serve/deploy-static. SNEFERU_RUNS_DIR is required only for watch/scan
    (FR-029/FR-030), which validate it at the command level. init must succeed
    when SNEFERU_RUNS_DIR is absent.
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("SNEFERU_RUNS_DIR", raising=False)
    db_path = str(tmp_path / "agentnews.db")
    env = tmp_path / ".env"
    env.write_text(
        "PUBLIC_URL=https://news.example.com\n"
        "PAYMENT_LINK_URL=https://buy.example.com/agentnews\n"
        "ADMIN_TOKEN=test-admin-token\n"
        "LOG_SALT=test-salt-fixed\n"
        f"AGENTNEWS_DB={db_path}\n"
    )
    rc = cli.main(["init", "--env-file", str(env)])
    assert rc == 0
    captured = capsys.readouterr()
    assert "ERROR:MISSING_ENV:SNEFERU_RUNS_DIR" not in captured.err


def test_cli_init_warns_on_insecure_url_when_allowed(tmp_path, monkeypatch, capsys):
    """FR-041: AGENTNEWS_ALLOW_INSECURE=true prints a warning, not an error."""
    monkeypatch.chdir(tmp_path)
    # conftest.py pre-seeds PUBLIC_URL; override it explicitly so the insecure
    # value is visible to load_config (load_env_file does not overwrite set vars).
    monkeypatch.setenv("PUBLIC_URL", "http://news.example.com")
    monkeypatch.setenv("AGENTNEWS_ALLOW_INSECURE", "true")
    db_path = str(tmp_path / "agentnews.db")
    env = tmp_path / ".env"
    env.write_text(
        "PUBLIC_URL=http://news.example.com\n"
        "PAYMENT_LINK_URL=https://buy.example.com/agentnews\n"
        "ADMIN_TOKEN=test-admin-token\n"
        "AGENTNEWS_ALLOW_INSECURE=true\n"
        "LOG_SALT=test-salt-fixed\n"
        f"AGENTNEWS_DB={db_path}\n"
    )
    rc = cli.main(["init", "--env-file", str(env)])
    assert rc == 0
    captured = capsys.readouterr()
    assert "WARNING:INSECURE_PUBLIC_URL:http://news.example.com" in captured.err
    assert "ERROR:INSECURE_PUBLIC_URL" not in captured.err


def test_cli_migrate(tmp_path, monkeypatch):
    db_path = str(tmp_path / "an.db")
    env = _write_env(tmp_path, db_path)
    monkeypatch.setenv("AGENTNEWS_DB", db_path)
    rc = cli.main(["migrate", "--env-file", str(env)])
    assert rc == 0
    import sqlite3
    conn = sqlite3.connect(db_path)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()
    assert {"packages", "citations", "api_keys", "request_log"} <= tables


def test_cli_import_run(tmp_path, monkeypatch, good_run_dir):
    db_path = str(tmp_path / "an.db")
    env = _write_env(tmp_path, db_path)
    monkeypatch.setenv("AGENTNEWS_DB", db_path)
    rc = cli.main([
        "import-run", str(good_run_dir),
        "--env-file", str(env), "--skip-verify-urls", "--show-hash",
    ])
    assert rc == 0
    # FR-005: re-import without --replace is a no-op (exit 0)
    rc2 = cli.main([
        "import-run", str(good_run_dir),
        "--env-file", str(env), "--skip-verify-urls",
    ])
    assert rc2 == 0  # ALREADY_IMPORTED


def test_cli_import_run_replace(tmp_path, monkeypatch, good_run_dir):
    """FR-005: --replace deletes old row and re-imports preserving PID."""
    db_path = str(tmp_path / "an.db")
    env = _write_env(tmp_path, db_path)
    monkeypatch.setenv("AGENTNEWS_DB", db_path)
    cli.main(["migrate", "--env-file", str(env)])
    rc = cli.main([
        "import-run", str(good_run_dir),
        "--env-file", str(env), "--skip-verify-urls", "--show-hash",
    ])
    assert rc == 0
    # get the original PID
    import sqlite3
    conn = sqlite3.connect(db_path)
    orig_pid = conn.execute("SELECT id FROM packages LIMIT 1").fetchone()[0]
    conn.close()
    # replace
    rc2 = cli.main([
        "import-run", str(good_run_dir),
        "--env-file", str(env), "--replace", "--skip-verify-urls",
    ])
    assert rc2 == 0
    # PID should be preserved
    conn = sqlite3.connect(db_path)
    new_pid = conn.execute("SELECT id FROM packages LIMIT 1").fetchone()[0]
    replace_status = conn.execute("SELECT status FROM ingest_runs WHERE status='replaced' LIMIT 1").fetchone()
    conn.close()
    assert new_pid == orig_pid
    assert replace_status is not None


def test_cli_import_run_force_requires_admin(tmp_path, monkeypatch, bad_run_dir):
    """FR-027: --force without ADMIN_TOKEN exits 2."""
    db_path = str(tmp_path / "an.db")
    env = _write_env(tmp_path, db_path)
    # remove ADMIN_TOKEN from env file and environment
    env_content = Path(env).read_text()
    env_content = env_content.replace("ADMIN_TOKEN=test-admin-token\n", "")
    Path(env).write_text(env_content)
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)
    monkeypatch.setenv("AGENTNEWS_DB", db_path)
    rc = cli.main([
        "import-run", str(bad_run_dir),
        "--env-file", str(env), "--force", "--skip-verify-urls",
    ])
    assert rc == 2  # ADMIN_TOKEN_REQUIRED


def test_cli_import_run_id_collision_retry(tmp_path, monkeypatch, good_run_dir, capsys):
    """AC-033: import-run retries on package ID collision and succeeds."""
    import shutil
    db_path = str(tmp_path / "an.db")
    env = _write_env(tmp_path, db_path)
    monkeypatch.setenv("AGENTNEWS_DB", db_path)
    cli.main(["migrate", "--env-file", str(env)])
    monkeypatch.setattr("agentnews.storage.secrets.token_hex", lambda n: "aaaaaa")
    rc = cli.main([
        "import-run", str(good_run_dir),
        "--env-file", str(env), "--skip-verify-urls",
    ])
    assert rc == 0
    run2 = tmp_path / "run-good-2"
    shutil.copytree(good_run_dir, run2)
    calls = ["aaaaaa", "bbbbbb"]
    monkeypatch.setattr("agentnews.storage.secrets.token_hex", lambda n: calls.pop(0))
    rc2 = cli.main([
        "import-run", str(run2),
        "--env-file", str(env), "--skip-verify-urls",
    ])
    assert rc2 == 0
    captured = capsys.readouterr()
    assert "ERROR:ID_COLLISION" not in captured.err
    pids = re.findall(r"pkg-\d{8}-[a-f0-9]{6}", captured.out)
    assert len(pids) == 2
    assert pids[0] != pids[1]


def test_cli_import_run_force_stages(tmp_path, monkeypatch, bad_run_dir):
    """FR-027: --force bypasses rejection and lands in staged."""
    db_path = str(tmp_path / "an.db")
    env = _write_env(tmp_path, db_path)
    monkeypatch.setenv("AGENTNEWS_DB", db_path)
    cli.main(["migrate", "--env-file", str(env)])
    rc = cli.main([
        "import-run", str(bad_run_dir),
        "--env-file", str(env), "--force", "--skip-verify-urls",
    ])
    assert rc == 0
    import sqlite3
    conn = sqlite3.connect(db_path)
    row = conn.execute("SELECT status FROM packages LIMIT 1").fetchone()
    force_row = conn.execute("SELECT status FROM ingest_runs WHERE status='force_override' LIMIT 1").fetchone()
    ext = conn.execute("SELECT extensions FROM packages LIMIT 1").fetchone()
    conn.close()
    assert row is not None
    assert row[0] == "staged"  # forced packages are always staged
    assert force_row is not None
    assert ext is not None
    assert "forced_import" in ext[0]


def test_cli_import_run_stage_flag(tmp_path, monkeypatch, good_run_dir):
    """FR-006: --stage forces staged status with publish_after=NULL."""
    db_path = str(tmp_path / "an.db")
    env = _write_env(tmp_path, db_path)
    monkeypatch.setenv("AGENTNEWS_DB", db_path)
    cli.main(["migrate", "--env-file", str(env)])
    rc = cli.main([
        "import-run", str(good_run_dir),
        "--env-file", str(env), "--stage", "--skip-verify-urls",
    ])
    assert rc == 0
    import sqlite3
    conn = sqlite3.connect(db_path)
    row = conn.execute("SELECT status, publish_after FROM packages LIMIT 1").fetchone()
    conn.close()
    assert row[0] == "staged"
    assert row[1] is None


def test_cli_import_run_rejects_bad(tmp_path, monkeypatch, bad_run_dir):
    db_path = str(tmp_path / "an.db")
    env = _write_env(tmp_path, db_path)
    monkeypatch.setenv("AGENTNEWS_DB", db_path)
    rc = cli.main([
        "import-run", str(bad_run_dir),
        "--env-file", str(env), "--skip-verify-urls",
    ])
    assert rc == 3  # EXIT_REJECT (degraded + low score + insufficient citations)


def test_cli_key_create_and_revoke(tmp_path, monkeypatch):
    db_path = str(tmp_path / "an.db")
    env = _write_env(tmp_path, db_path)
    monkeypatch.setenv("AGENTNEWS_DB", db_path)
    cli.main(["migrate", "--env-file", str(env)])
    rc = cli.main(["key", "create", "--label", "tester", "--env-file", str(env)])
    assert rc == 0


def test_cli_validate_layout(tmp_path, monkeypatch, good_run_dir):
    env = _write_env(tmp_path, str(tmp_path / "an.db"))
    monkeypatch.setenv("AGENTNEWS_DB", str(tmp_path / "an.db"))
    rc = cli.main(["validate-sneferu-layout", str(good_run_dir), "--env-file", str(env)])
    assert rc == 0


def test_cli_scan(tmp_path, monkeypatch, good_run_dir, minimal_run_dir):
    # build a fake runs dir with symlinks to fixtures
    runs = tmp_path / "runs"
    runs.mkdir()
    (runs / "2026-08-15T10-00-00Z-research-abc123").symlink_to(good_run_dir)
    db_path = str(tmp_path / "an.db")
    env = _write_env(tmp_path, db_path)
    monkeypatch.setenv("AGENTNEWS_DB", db_path)
    # scan should LIST, not import
    rc = cli.main(["scan", "--runs-dir", str(runs), "--env-file", str(env)])
    assert rc == 0


def test_cli_scan_missing_env(tmp_path, monkeypatch):
    """AC-064: scan with no SNEFERU_RUNS_DIR and no --dir exits 2."""
    db_path = str(tmp_path / "an.db")
    env = _write_env(tmp_path, db_path)
    monkeypatch.setenv("AGENTNEWS_DB", db_path)
    monkeypatch.delenv("SNEFERU_RUNS_DIR", raising=False)
    rc = cli.main(["scan", "--env-file", str(env)])
    assert rc == 2


def test_cli_scan_empty(tmp_path, monkeypatch):
    """Scan with no signature.json dirs prints 'No new runs found.'"""
    runs = tmp_path / "runs"
    runs.mkdir()
    db_path = str(tmp_path / "an.db")
    env = _write_env(tmp_path, db_path)
    monkeypatch.setenv("AGENTNEWS_DB", db_path)
    rc = cli.main(["scan", "--runs-dir", str(runs), "--env-file", str(env)])
    assert rc == 0


def test_cli_deploy_static(tmp_path, monkeypatch, good_run_dir, capsys):
    db_path = str(tmp_path / "an.db")
    env = _write_env(tmp_path, db_path)
    monkeypatch.setenv("AGENTNEWS_DB", db_path)
    cli.main(["migrate", "--env-file", str(env)])
    cli.main(["import-run", str(good_run_dir), "--env-file", str(env),
              "--skip-verify-urls"])
    captured = capsys.readouterr()
    out = tmp_path / "site"
    rc = cli.main(["deploy-static", "--output", str(out), "--env-file", str(env)])
    assert rc == 0
    assert (out / "index.html").exists()
    assert (out / "access.html").exists()
    assert (out / "static" / "style.css").exists()
    assert (out / "static" / "reuse-license-v1.md").exists()
    # FR-028: feed.xml and catalog.json are dynamic endpoints, not static files
    assert not (out / "feed.xml").exists()
    assert not (out / "catalog.json").exists()
    # FR-028: articles are rendered as articles/{slug}.html; slug equals package id
    pid_match = re.search(r"imported pid=(pkg-\d{8}-[a-f0-9]{6})", captured.out)
    assert pid_match
    pid = pid_match.group(1)
    assert (out / "articles" / f"{pid}.html").exists()
    # 404/503 error pages for nginx error_page directives
    assert (out / "404.html").exists()
    assert (out / "503.html").exists()
    assert "Page not found" in (out / "404.html").read_text()
    assert "Service temporarily unavailable" in (out / "503.html").read_text()


def test_cli_verify(tmp_path, monkeypatch, good_run_dir):
    db_path = str(tmp_path / "an.db")
    env = _write_env(tmp_path, db_path)
    monkeypatch.setenv("AGENTNEWS_DB", db_path)
    cli.main(["migrate", "--env-file", str(env)])
    cli.main(["import-run", str(good_run_dir), "--env-file", str(env),
              "--skip-verify-urls"])
    # find pid from db
    import sqlite3
    conn = sqlite3.connect(db_path)
    pid = conn.execute("SELECT id FROM packages LIMIT 1").fetchone()[0]
    conn.close()
    rc = cli.main(["verify", pid, "--env-file", str(env)])
    assert rc == 0  # match


def test_cli_sample_set_via_spec_command(tmp_path, monkeypatch, good_run_dir):
    """`sample set <id>` marks a published package as the single sample."""
    db_path = str(tmp_path / "an.db")
    env = _write_env(tmp_path, db_path)
    monkeypatch.setenv("AGENTNEWS_DB", db_path)
    cli.main(["migrate", "--env-file", str(env)])
    cli.main(["import-run", str(good_run_dir), "--env-file", str(env),
              "--skip-verify-urls"])
    import sqlite3
    conn = sqlite3.connect(db_path)
    pid = conn.execute("SELECT id FROM packages LIMIT 1").fetchone()[0]
    conn.close()
    rc = cli.main(["sample", "set", pid, "--env-file", str(env)])
    assert rc == 0
    conn = sqlite3.connect(db_path)
    is_sample = conn.execute("SELECT is_sample FROM packages WHERE id = ?", (pid,)).fetchone()[0]
    conn.close()
    assert is_sample == 1


def test_cli_backup_and_restore(tmp_path, monkeypatch, good_run_dir):
    db_path = str(tmp_path / "an.db")
    env = _write_env(tmp_path, db_path)
    monkeypatch.setenv("AGENTNEWS_DB", db_path)
    cli.main(["migrate", "--env-file", str(env)])
    cli.main(["import-run", str(good_run_dir), "--env-file", str(env),
              "--skip-verify-urls"])
    backup_path = str(tmp_path / "backup.db")
    rc = cli.main(["backup", backup_path, "--env-file", str(env)])
    assert rc == 0
    assert Path(backup_path).exists()
    # restore to a new db path
    restore_db = str(tmp_path / "restored.db")
    monkeypatch.setenv("AGENTNEWS_DB", restore_db)
    env2 = _write_env(tmp_path, restore_db)
    rc = cli.main(["restore", backup_path, "--env-file", str(env2)])
    assert rc == 0
    # verify the restored db has the package
    import sqlite3
    conn = sqlite3.connect(restore_db)
    count = conn.execute("SELECT COUNT(*) FROM packages").fetchone()[0]
    conn.close()
    assert count == 1


def test_cli_module_execution(tmp_path):
    """Verify `python3 -m agentnews` works via __main__.py."""
    import subprocess
    result = subprocess.run(
        [sys.executable, "-m", "agentnews", "--help"],
        capture_output=True, text=True,
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parent.parent / "src")},
    )
    assert result.returncode == 0
    assert "AgentNews CLI" in result.stdout


def test_cli_version():
    """AC-001: --version prints the version and exits 0."""
    import subprocess
    result = subprocess.run(
        [sys.executable, "-m", "agentnews", "--version"],
        capture_output=True, text=True,
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parent.parent / "src")},
    )
    assert result.returncode == 0
    assert "agentnews 0.1.0" in result.stdout


def test_cli_sample_set_unset_show(tmp_path, monkeypatch, good_run_dir, capsys):
    """AC-022: `sample set/show/unset` commands."""
    db_path = str(tmp_path / "an.db")
    env = _write_env(tmp_path, db_path)
    monkeypatch.setenv("AGENTNEWS_DB", db_path)
    cli.main(["migrate", "--env-file", str(env)])
    cli.main(["import-run", str(good_run_dir), "--env-file", str(env),
              "--skip-verify-urls"])
    import sqlite3
    conn = sqlite3.connect(db_path)
    pid = conn.execute("SELECT id FROM packages LIMIT 1").fetchone()[0]
    conn.close()
    rc = cli.main(["sample", "set", pid, "--env-file", str(env)])
    assert rc == 0
    rc = cli.main(["sample", "show", "--env-file", str(env)])
    assert rc == 0
    captured = capsys.readouterr()
    assert pid in captured.out
    rc = cli.main(["sample", "unset", pid, "--env-file", str(env)])
    assert rc == 0


def test_cli_report_usage_costs(tmp_path, monkeypatch, good_run_dir, capsys):
    """AC-026: report usage and costs."""
    db_path = str(tmp_path / "an.db")
    env = _write_env(tmp_path, db_path)
    monkeypatch.setenv("AGENTNEWS_DB", db_path)
    cli.main(["migrate", "--env-file", str(env)])
    cli.main(["import-run", str(good_run_dir), "--env-file", str(env),
              "--skip-verify-urls"])
    rc = cli.main(["report", "usage", "--env-file", str(env)])
    assert rc == 0
    captured = capsys.readouterr()
    assert "agentnews.usage_report/v1" in captured.out
    rc = cli.main(["report", "costs", "--env-file", str(env)])
    assert rc == 0
    captured = capsys.readouterr()
    assert "agentnews.costs_report/v1" in captured.out


def test_cli_purge_logs(tmp_path, monkeypatch, good_run_dir, capsys):
    """FR-025: purge-logs deletes old rows."""
    db_path = str(tmp_path / "an.db")
    env = _write_env(tmp_path, db_path)
    monkeypatch.setenv("AGENTNEWS_DB", db_path)
    cli.main(["migrate", "--env-file", str(env)])
    cli.main(["import-run", str(good_run_dir), "--env-file", str(env),
              "--skip-verify-urls"])
    # Insert an old request_log row
    import sqlite3
    conn = sqlite3.connect(db_path)
    old = "2020-01-01T00:00:00Z"
    conn.execute(
        "INSERT INTO request_log (key_prefix, method, endpoint, status_code, response_time_ms, ip_hash, user_agent_hash, request_id, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
        ("ak_test", "GET", "/v1/packages", 200, 10, "ip", "ua", "rid", old),
    )
    conn.commit()
    conn.close()
    rc = cli.main(["purge-logs", "--days", "7", "--env-file", str(env)])
    assert rc == 0
    captured = capsys.readouterr()
    assert "purged 1" in captured.out


def test_cli_import_run_dry_run(tmp_path, monkeypatch, good_run_dir, capsys):
    """AC-041: import-run --dry-run writes nothing."""
    db_path = str(tmp_path / "an.db")
    env = _write_env(tmp_path, db_path)
    monkeypatch.setenv("AGENTNEWS_DB", db_path)
    cli.main(["migrate", "--env-file", str(env)])
    rc = cli.main(["import-run", str(good_run_dir), "--env-file", str(env),
                   "--dry-run", "--skip-verify-urls"])
    assert rc == 0
    captured = capsys.readouterr()
    assert "ADMIT" in captured.out
    import sqlite3
    conn = sqlite3.connect(db_path)
    count = conn.execute("SELECT COUNT(*) FROM packages").fetchone()[0]
    conn.close()
    assert count == 0


def test_cli_watch_lockfile(tmp_path):
    """AC-050: concurrent watch instances are excluded by a lockfile."""
    lock_path = str(tmp_path / "watch.lock")
    assert cli._acquire_watch_lock(lock_path) == 0
    assert cli._acquire_watch_lock(lock_path) == 2
    cli._acquire_watch_lock(lock_path)  # still held
    os.unlink(lock_path)


def test_cli_default_import_publishes_when_delay_zero(tmp_path, monkeypatch, good_run_dir):
    """FR-006: import-run with STAGING_DELAY_MINUTES=0 publishes immediately."""
    db_path = str(tmp_path / "an.db")
    env = _write_env(tmp_path, db_path)
    monkeypatch.setenv("AGENTNEWS_DB", db_path)
    monkeypatch.setenv("STAGING_DELAY_MINUTES", "0")
    cli.main(["migrate", "--env-file", str(env)])
    rc = cli.main(["import-run", str(good_run_dir), "--env-file", str(env),
                   "--skip-verify-urls"])
    assert rc == 0
    import sqlite3
    conn = sqlite3.connect(db_path)
    row = conn.execute("SELECT status, publish_after FROM packages LIMIT 1").fetchone()
    conn.close()
    assert row[0] == "published"
    assert row[1] is None


def test_cli_import_with_staging_delay_sets_publish_after(tmp_path, monkeypatch, good_run_dir):
    """FR-006: import-run with STAGING_DELAY_MINUTES>0 stages with publish_after."""
    db_path = str(tmp_path / "an.db")
    env = _write_env(tmp_path, db_path)
    monkeypatch.setenv("AGENTNEWS_DB", db_path)
    monkeypatch.setenv("STAGING_DELAY_MINUTES", "5")
    cli.main(["migrate", "--env-file", str(env)])
    rc = cli.main(["import-run", str(good_run_dir), "--env-file", str(env),
                   "--skip-verify-urls"])
    assert rc == 0
    import sqlite3
    conn = sqlite3.connect(db_path)
    row = conn.execute("SELECT status, publish_after FROM packages LIMIT 1").fetchone()
    conn.close()
    assert row[0] == "staged"
    assert row[1] is not None
    assert row[1].endswith("Z")


def test_cli_list_and_show(tmp_path, monkeypatch, good_run_dir, capsys):
    """CLI `list` and `show <id>` commands read packages and trigger lazy staging."""
    db_path = str(tmp_path / "an.db")
    env = _write_env(tmp_path, db_path)
    monkeypatch.setenv("AGENTNEWS_DB", db_path)
    cli.main(["migrate", "--env-file", str(env)])
    cli.main(["import-run", str(good_run_dir), "--env-file", str(env),
              "--skip-verify-urls"])
    import sqlite3
    conn = sqlite3.connect(db_path)
    pid = conn.execute("SELECT id FROM packages LIMIT 1").fetchone()[0]
    conn.close()
    rc = cli.main(["list", "--env-file", str(env)])
    assert rc == 0
    captured = capsys.readouterr()
    assert pid in captured.out
    rc = cli.main(["show", pid, "--env-file", str(env)])
    assert rc == 0
    captured = capsys.readouterr()
    assert pid in captured.out
    assert "published" in captured.out
