"""SQLite access layer (FR-034, FR-026, FR-025, §5.1, §5.2).

Every connection opens with WAL, busy_timeout=5000, foreign_keys=ON.
Migrations are forward-only SQL files with SHA-256 checksums, wrapped in
transactions. The server writes a PID file at ``AGENTNEWS_DB + '.pid'``.
"""
from __future__ import annotations

import hashlib
import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Iterable, List, Optional, Sequence, Tuple

from .config import Config

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


def _set_pragmas(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")


def connect(db_path: str, check_same_thread: bool = False) -> sqlite3.Connection:
    """Open a connection with the required pragmas (FR-034).

    ``check_same_thread=False`` is needed because the ASGI server (and
    Starlette's TestClient) handles requests in a thread-pool separate from
    the connection-creation thread.  SQLite + WAL handles concurrent reads;
    writes are serialized via ``busy_timeout``.
    """
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=5.0, check_same_thread=check_same_thread)
    conn.row_factory = sqlite3.Row
    _set_pragmas(conn)
    return conn


def migration_files() -> List[Path]:
    return sorted(MIGRATIONS_DIR.glob("*.sql"))


def _migration_version(path: Path) -> int:
    return int(path.stem.split("_")[0])


def _checksum(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def applied_migrations(conn: sqlite3.Connection) -> dict:
    try:
        rows = conn.execute(
            "SELECT version, checksum FROM schema_migrations"
        ).fetchall()
    except sqlite3.OperationalError:
        return {}
    return {int(r["version"]): r["checksum"] for r in rows}


def _split_sql_statements(text: str) -> List[str]:
    """Split SQL text into individual statements on semicolons.

    Strips comment-only lines.  Naive but sufficient for our forward-only
    migration files (no triggers or multi-line string literals).
    """
    statements: List[str] = []
    current: List[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("--"):
            continue
        current.append(line)
        if stripped.endswith(";"):
            stmt = "\n".join(current).strip()
            if stmt:
                statements.append(stmt)
            current = []
    tail = "\n".join(current).strip()
    if tail:
        statements.append(tail)
    return statements


def run_migrations(conn: sqlite3.Connection) -> List[int]:
    """Apply pending migrations (FR-026). Returns applied version numbers."""
    applied: List[int] = []
    ensure_schema_migrations_table(conn)
    known = applied_migrations(conn)
    for path in migration_files():
        version = _migration_version(path)
        text = path.read_text(encoding="utf-8")
        checksum = _checksum(text)
        if version in known:
            continue
        try:
            conn.execute("BEGIN")
            for stmt in _split_sql_statements(text):
                conn.execute(stmt)
            conn.execute(
                "INSERT INTO schema_migrations (version, checksum) VALUES (?, ?)",
                (version, checksum),
            )
            conn.execute("COMMIT")
            applied.append(version)
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.OperationalError:
                pass  # no active transaction (e.g. BEGIN failed)
            raise
        conn.execute("PRAGMA foreign_keys=ON")
    return applied


def ensure_schema_migrations_table(conn: sqlite3.Connection) -> None:
    """The 0001 migration creates schema_migrations; the runner needs it to exist
    before it can query. Create it idempotently if the migration hasn't run."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        " version INTEGER PRIMARY KEY,"
        " checksum TEXT NOT NULL,"
        " applied_at TEXT NOT NULL DEFAULT (datetime('now'))"
        ")"
    )
    conn.commit()


def has_all_tables(conn: sqlite3.Connection) -> bool:
    required = {
        "schema_migrations",
        "packages",
        "citations",
        "api_keys",
        "request_log",
        "ingest_runs",
    }
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()
    present = {r["name"] for r in rows}
    return required.issubset(present)


# --------------------------------------------------------------------------- #
# PID file management (FR-025, FR-037)
# --------------------------------------------------------------------------- #

def pid_file_path(db_path: str) -> str:
    return db_path + ".pid"


def write_pid_file(db_path: str) -> None:
    try:
        with open(pid_file_path(db_path), "w", encoding="utf-8") as fh:
            fh.write(str(os.getpid()))
    except OSError:
        pass


def remove_pid_file(db_path: str) -> None:
    try:
        os.remove(pid_file_path(db_path))
    except FileNotFoundError:
        pass


def server_running(db_path: str) -> bool:
    """Return True if a live server PID file exists (FR-025)."""
    path = pid_file_path(db_path)
    if not os.path.exists(path):
        return False
    try:
        with open(path, "r", encoding="utf-8") as fh:
            pid = int(fh.read().strip())
    except (ValueError, OSError):
        return False
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


# --------------------------------------------------------------------------- #
# Query helpers
# --------------------------------------------------------------------------- #

def query_one(conn: sqlite3.Connection, sql: str, params: Sequence[Any] = ()) -> Optional[sqlite3.Row]:
    return conn.execute(sql, tuple(params)).fetchone()


def query_all(conn: sqlite3.Connection, sql: str, params: Sequence[Any] = ()) -> List[sqlite3.Row]:
    return conn.execute(sql, tuple(params)).fetchall()


def execute(conn: sqlite3.Connection, sql: str, params: Sequence[Any] = ()) -> sqlite3.Cursor:
    cur = conn.execute(sql, tuple(params))
    conn.commit()
    return cur


def wal_checkpoint(conn: sqlite3.Connection) -> None:
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except sqlite3.OperationalError:
        pass


def count_packages(conn: sqlite3.Connection) -> int:
    """FR-019: health check reports count of published packages only."""
    row = query_one(conn, "SELECT COUNT(*) AS c FROM packages WHERE status='published'")
    return int(row["c"]) if row else 0


def db_ok(db_path: str) -> bool:
    try:
        conn = connect(db_path)
        try:
            conn.execute("SELECT 1").fetchone()
            return True
        finally:
            conn.close()
    except sqlite3.Error:
        return False


# --------------------------------------------------------------------------- #
# Threadpool wrapper for FastAPI request handlers (FR-034)
# --------------------------------------------------------------------------- #

def run_in_threadpool(func, *args, **kwargs):
    """Run a sync DB function off the event loop.

    Uses starlette.concurrency.run_in_threadpool when available (FastAPI env),
    falls back to a stdlib thread executor for CLI/test use.
    """
    try:
        from starlette.concurrency import run_in_threadpool as _rip  # type: ignore

        return _rip(func, *args, **kwargs)
    except Exception:  # pragma: no cover - sync fallback
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            fut = ex.submit(func, *args, **kwargs)
            return fut.result()


def retry_on_lock(func, retries: int = 1):
    """Wrap a DB-write callable: on ``database is locked`` retry once (FR-034)."""
    last_exc: Optional[Exception] = None
    for _ in range(retries + 1):
        try:
            return func()
        except sqlite3.OperationalError as exc:
            if "locked" in str(exc).lower():
                last_exc = exc
                time.sleep(0.2)
                continue
            raise
    raise sqlite3.OperationalError(f"ERROR:DB_LOCKED:{last_exc}")
