#!/usr/bin/env python3
"""runtime_serve.py — seed + serve AgentNews for the Sneferu runtime-test phase.

The runtime-test harness spawns this script with a free port, polls the
configured health endpoint, then runs the declared HTTP smoke plan against the
live service. This script therefore creates a fresh, deterministic database,
imports the checked-in run-good fixture, marks it as the sample, and inserts a
known runtime-test API key so the smoke plan can exercise the paid subscriber
endpoints.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Ensure the source package is importable even if the editable install has not
# yet completed (e.g. during local pytest runs that drive the script directly).
REPO_ROOT = Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import uvicorn  # noqa: E402

from agentnews import db, ingest, storage  # noqa: E402
from agentnews.config import load_config  # noqa: E402

# Deterministic runtime-test API key. The format must match auth.KEY_RE
# (``ak_<32 hex chars>``). This key is inserted into the throwaway runtime-test
# database and is referenced verbatim from test_plan.md.
RUNTIME_TEST_KEY = "ak_00000000000000000000000000000001"
RUNTIME_TEST_KEY_LABEL = "runtime-test"
RUNTIME_TEST_PACKAGE_ID = "pkg-runtime-test-0001"
FIXTURE_RUN = REPO_ROOT / "tests" / "fixtures" / "run-good"


def _require_fixture() -> str:
    """Return the absolute path to the run-good fixture, failing loudly if absent."""
    if not FIXTURE_RUN.is_dir():
        raise FileNotFoundError(
            f"runtime fixture not found: {FIXTURE_RUN}. "
            "The runtime-test seed requires tests/fixtures/run-good."
        )
    return str(FIXTURE_RUN)


def _remove_db_files(db_path: Path) -> None:
    """Remove a previous SQLite DB plus any WAL/SHM siblings so the seed is fresh."""
    for suffix in ("", "-wal", "-shm"):
        path = db_path.with_name(db_path.name + suffix)
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def seed_runtime_database(config) -> None:
    """Create a fresh DB, migrate, import the fixture, and insert the test key."""
    fixture_dir = _require_fixture()
    _remove_db_files(Path(config.db_path))

    conn = db.connect(config.db_path)
    try:
        db.run_migrations(conn)
        parsed = ingest.parse_run(Path(fixture_dir), config)
        # Use a deterministic package ID so the static test_plan.md can reference
        # exact paths (/v1/packages/{pid}, /articles/{slug}) without guessing.
        pid = storage.insert_package(
            conn, parsed, config, is_sample=True,
            pid_override=RUNTIME_TEST_PACKAGE_ID,
        )
        storage.transition_to_published(conn, pid, config)
        # insert_package with is_sample=True already marks the sample; this is
        # defensive and idempotent.
        storage.mark_sample(conn, pid)
        # Insert a deterministic runtime-test key so the HTTP smoke plan can
        # authenticate without inventing credentials.
        storage.insert_api_key(conn, RUNTIME_TEST_KEY, RUNTIME_TEST_KEY_LABEL, config)
        conn.commit()
    finally:
        conn.close()


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: runtime_serve.py <port>", file=sys.stderr)
        return 2
    try:
        port = int(sys.argv[1])
    except ValueError:
        print(f"invalid port: {sys.argv[1]!r}", file=sys.stderr)
        return 2

    # Runtime-test environment: use a throwaway DB inside the workspace unless
    # the caller (e.g. a local test) explicitly points AGENTNEWS_DB elsewhere.
    data_dir = REPO_ROOT / "data"
    data_dir.mkdir(exist_ok=True)
    default_db = str(data_dir / "agentnews.db")

    os.environ.setdefault("PUBLIC_URL", f"http://127.0.0.1:{port}")
    os.environ.setdefault("PAYMENT_LINK_URL", "https://buy.example.com/agentnews")
    os.environ.setdefault("ADMIN_TOKEN", "runtime-test-admin-token")
    os.environ.setdefault("AGENTNEWS_DB", default_db)
    os.environ.setdefault("RATE_LIMIT_PER_DAY", "100")
    os.environ.setdefault("BURST_ALLOWANCE", "20")
    os.environ.setdefault("AGENTNEWS_ALLOW_INSECURE", "true")
    os.environ.setdefault("LOG_SALT", "runtime-test-log-salt")

    config = load_config()
    seed_runtime_database(config)

    # uvicorn.run blocks until SIGTERM/SIGINT, then returns.
    uvicorn.run(
        "agentnews.api:app",
        host="127.0.0.1",
        port=port,
        timeout_keep_alive=30,
        timeout_graceful_shutdown=10,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
