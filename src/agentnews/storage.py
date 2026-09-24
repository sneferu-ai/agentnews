"""Storage operations bridging the parser and the SQLite database.

Owns the package lifecycle (FR-004, FR-006, FR-018, FR-019, FR-021, FR-022,
FR-030, FR-043), slug generation, the single-sample constraint, API key
records, request logging, and content-hash recomputation for verify.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import secrets
import sqlite3
import time
from typing import Any, Dict, List, Optional, Tuple

from .config import Config
from .ingest import ParsedRun
from .render import build_reuse_terms, compute_latency, now_iso


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def generate_package_id() -> str:
    """FR-004: package IDs have the form pkg-YYYYMMDD-xxxxxx."""
    today = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%d")
    suffix = secrets.token_hex(3)
    return f"pkg-{today}-{suffix}"


def _serialize_sig(signature: Dict[str, Any]) -> str:
    return json.dumps(signature, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _count_published(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT COUNT(*) AS c FROM packages WHERE status='published'").fetchone()
    return int(row["c"]) if row else 0


def _count_sample(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT COUNT(*) AS c FROM packages WHERE is_sample=1").fetchone()
    return int(row["c"]) if row else 0


# --------------------------------------------------------------------------- #
# Insert
# --------------------------------------------------------------------------- #

class DuplicateRunError(Exception):
    pass


class SampleLimitError(Exception):
    pass


class IdCollisionError(Exception):
    pass


class SampleRaceError(Exception):
    pass


def _insert_package_and_citations(
    conn: sqlite3.Connection,
    parsed: ParsedRun,
    config: Config,
    pid: str,
    slug: str,
    is_sample: bool,
) -> None:
    """Single-shot insert of the package row and its citations."""
    reuse_terms = build_reuse_terms(config)
    conn.execute(
        """INSERT INTO packages
           (id, run_id, run_type, question, summary, findings, method_notes,
            quality_score, signature, reuse_terms, content_hash, slug, status,
            is_sample, latency_disclosure, cost_data, run_started_at,
            run_completed_at, extensions)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,JSON(?),?,?,JSON(?))""",
        (
            pid,
            parsed.run_id,
            parsed.run_type,
            parsed.question,
            parsed.summary,
            parsed.findings,
            parsed.method_notes,
            parsed.quality_score,
            _serialize_sig(parsed.signature),
            json.dumps(reuse_terms, sort_keys=True, separators=(",", ":"), ensure_ascii=False),
            parsed.content_hash,
            slug,
            "imported",
            1 if is_sample else 0,
            "This research package is pending publication. Latency disclosure "
            "will appear once it is published.",
            json.dumps(parsed.cost_data) if parsed.cost_data is not None else None,
            parsed.run_started_at,
            parsed.run_completed_at,
            json.dumps(parsed.extensions, sort_keys=True, separators=(",", ":"), ensure_ascii=False),
        ),
    )
    for c in parsed.citations:
        conn.execute(
            """INSERT INTO citations
               (package_id, ordinal, source_type, title, url, authors, year,
                verified, excerpt, accessed_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (pid, c.ordinal, c.source_type, c.title, c.url, c.authors, c.year,
             c.verified, c.excerpt, c.accessed_at),
        )
    conn.commit()


def insert_package(
    conn: sqlite3.Connection,
    parsed: ParsedRun,
    config: Config,
    is_sample: bool = False,
    pid_override: Optional[str] = None,
) -> str:
    """FR-004: insert a parsed run as 'imported'. Raises DuplicateRunError on dup run_id.

    If *pid_override* is given (FR-005 ``--replace``), use it instead of generating
    a new package ID, and skip the duplicate-run_id check (caller already deleted
    the old row).
    """
    if pid_override is None:
        existing = conn.execute("SELECT id FROM packages WHERE run_id = ?", (parsed.run_id,)).fetchone()
        if existing is not None:
            raise DuplicateRunError(parsed.run_id)
    if is_sample and _count_sample(conn) >= 1:
        raise SampleLimitError("ERROR:SAMPLE_LIMIT_REACHED")

    if pid_override is not None:
        pid = pid_override
        slug = pid
        _insert_package_and_citations(conn, parsed, config, pid, slug, is_sample)
        return pid

    # FR-004: generate pkg-YYYYMMDD-xxxxxx and retry up to 3 times on ID collision.
    last_error: Optional[sqlite3.IntegrityError] = None
    for _ in range(3):
        pid = generate_package_id()
        slug = pid
        try:
            _insert_package_and_citations(conn, parsed, config, pid, slug, is_sample)
            return pid
        except sqlite3.IntegrityError as exc:
            last_error = exc
            # Since slug is identical to the package id, a collision may surface
            # on either the primary key or the unique slug constraint.
            if "packages.id" in str(exc) or "packages.slug" in str(exc):
                conn.rollback()
                continue
            raise
    raise IdCollisionError(str(last_error))


def delete_package(conn: sqlite3.Connection, pid: str) -> None:
    """FR-005: delete a package and its citations (for ``--replace``)."""
    conn.execute("DELETE FROM citations WHERE package_id = ?", (pid,))
    conn.execute("DELETE FROM packages WHERE id = ?", (pid,))
    conn.commit()


# --------------------------------------------------------------------------- #
# Lifecycle transitions
# --------------------------------------------------------------------------- #

def _require_package(conn: sqlite3.Connection, pid: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM packages WHERE id = ?", (pid,)).fetchone()
    if row is None:
        raise KeyError(pid)
    return row


def transition_to_staged(conn: sqlite3.Connection, pid: str, publish_after: Optional[str] = None) -> None:
    """FR-006/FR-027: move a package to staged status."""
    conn.execute(
        "UPDATE packages SET status='staged', publish_after=? WHERE id=?",
        (publish_after, pid),
    )
    conn.commit()


def transition_to_published(conn: sqlite3.Connection, pid: str, config: Config) -> None:
    """FR-006/FR-015: imported/pending/staged -> published; compute latency disclosure."""
    row = _require_package(conn, pid)
    if row["status"] not in ("imported", "pending", "staged"):
        raise ValueError(f"ERROR:INVALID_TRANSITION:{row['status']}->published")
    pub_at = now_iso()
    hours, disclosure = compute_latency(row["run_completed_at"], pub_at)
    conn.execute(
        "UPDATE packages SET status='published', published_at=?, latency_hours=?, "
        "latency_disclosure=? WHERE id=?",
        (pub_at, hours, disclosure, pid),
    )
    conn.commit()


def transition_staged_to_published(conn: sqlite3.Connection, config: Config) -> int:
    """FR-006: lazily transition staged packages whose publish_after has passed.

    Called on each API request to the five trigger endpoints and on CLI
    list/show calls. Returns the number of packages transitioned.
    """
    import datetime as _dt
    now = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = conn.execute(
        "SELECT id FROM packages WHERE status='staged' "
        "AND publish_after IS NOT NULL AND publish_after <= ?",
        (now,),
    ).fetchall()
    count = 0
    for r in rows:
        pid = r["id"]
        row = conn.execute("SELECT * FROM packages WHERE id=?", (pid,)).fetchone()
        pub_at = now_iso()
        hours, disclosure = compute_latency(row["run_completed_at"], pub_at)
        conn.execute(
            "UPDATE packages SET status='published', published_at=?, latency_hours=?, "
            "latency_disclosure=? WHERE id=?",
            (pub_at, hours, disclosure, pid),
        )
        count += 1
    if count:
        conn.commit()
    return count


def withhold_package(conn: sqlite3.Connection, pid: str) -> None:
    """FR-018: published -> withheld. Only from published."""
    row = _require_package(conn, pid)
    if row["status"] != "published":
        raise ValueError(f"ERROR:NOT_PUBLISHED:{pid}")
    conn.execute(
        "UPDATE packages SET status='withheld', withheld_at=? WHERE id=?",
        (now_iso(), pid),
    )
    conn.commit()


def republish_package(conn: sqlite3.Connection, pid: str, config: Config) -> None:
    """FR-021: withheld -> published; refresh published_at and latency fields (FR-015)."""
    row = _require_package(conn, pid)
    if row["status"] != "withheld":
        raise ValueError(f"ERROR:NOT_WITHHELD:{pid}")
    pub_at = now_iso()
    hours, disclosure = compute_latency(row["run_completed_at"], pub_at)
    conn.execute(
        "UPDATE packages SET status='published', withheld_at=NULL, "
        "published_at=?, latency_hours=?, latency_disclosure=? WHERE id=?",
        (pub_at, hours, disclosure, pid),
    )
    conn.commit()


def mark_sample(conn: sqlite3.Connection, pid: str) -> None:
    """FR-022: mark a package as the single sample.

    Sets is_sample=0 on all existing rows, then is_sample=1 on the target
    row, in a single transaction. If a concurrent transaction wins the
    partial unique index, raises SampleRaceError.
    """
    row = _require_package(conn, pid)
    conn.execute("UPDATE packages SET is_sample=0 WHERE is_sample=1")
    try:
        conn.execute("UPDATE packages SET is_sample=1 WHERE id=?", (pid,))
    except sqlite3.IntegrityError as exc:
        conn.rollback()
        raise SampleRaceError(str(exc))
    conn.commit()


def unmark_sample(conn: sqlite3.Connection, pid: str) -> None:
    conn.execute("UPDATE packages SET is_sample=0 WHERE id=?", (pid,))
    conn.commit()


# --------------------------------------------------------------------------- #
# Reads
# --------------------------------------------------------------------------- #

def row_to_package(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "id": row["id"],
        "run_id": row["run_id"],
        "run_type": row["run_type"],
        "question": row["question"],
        "summary": row["summary"],
        "findings": row["findings"],
        "method_notes": row["method_notes"],
        "quality_score": row["quality_score"],
        "signature": json.loads(row["signature"]) if row["signature"] else {},
        "reuse_terms": json.loads(row["reuse_terms"]) if row["reuse_terms"] else {},
        "content_hash": row["content_hash"],
        "slug": row["slug"],
        "status": row["status"],
        "is_sample": bool(row["is_sample"]),
        "publish_after": row["publish_after"],
        "latency_hours": row["latency_hours"],
        "latency_disclosure": row["latency_disclosure"],
        "cost_data": json.loads(row["cost_data"]) if row["cost_data"] else None,
        "run_started_at": row["run_started_at"],
        "run_completed_at": row["run_completed_at"],
        "created_at": row["created_at"],
        "published_at": row["published_at"],
        "withheld_at": row["withheld_at"],
        "extensions": json.loads(row["extensions"]) if row["extensions"] else {},
    }


def get_package(conn: sqlite3.Connection, pid: str) -> Optional[Dict[str, Any]]:
    row = conn.execute("SELECT * FROM packages WHERE id = ?", (pid,)).fetchone()
    return row_to_package(row) if row else None


def get_package_id_by_slug(conn: sqlite3.Connection, slug: str) -> Optional[str]:
    """Return package id for a slug, or None if not found."""
    row = conn.execute("SELECT id FROM packages WHERE slug = ?", (slug,)).fetchone()
    return row["id"] if row else None


def get_package_by_run(conn: sqlite3.Connection, run_id: str) -> Optional[Dict[str, Any]]:
    row = conn.execute("SELECT * FROM packages WHERE run_id = ?", (run_id,)).fetchone()
    return row_to_package(row) if row else None


def list_packages(
    conn: sqlite3.Connection,
    limit: int = 20,
    offset: int = 0,
    status: str = "published",
    include_sample: bool = True,
) -> List[Dict[str, Any]]:
    sql = "SELECT * FROM packages WHERE status = ?"
    params: List[Any] = [status]
    if not include_sample:
        sql += " AND is_sample = 0"
    sql += " ORDER BY published_at DESC NULLS LAST, created_at DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    rows = conn.execute(sql, tuple(params)).fetchall()
    return [row_to_package(r) for r in rows]


def count_packages(
    conn: sqlite3.Connection,
    status: str = "published",
    include_sample: bool = True,
) -> int:
    """Return total package count matching the same filters as list_packages."""
    sql = "SELECT COUNT(*) FROM packages WHERE status = ?"
    params: List[Any] = [status]
    if not include_sample:
        sql += " AND is_sample = 0"
    row = conn.execute(sql, tuple(params)).fetchone()
    return int(row[0]) if row else 0


def get_citations(conn: sqlite3.Connection, pid: str) -> List[Dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM citations WHERE package_id = ? ORDER BY ordinal", (pid,)
    ).fetchall()
    out = []
    for r in rows:
        out.append({
            "ordinal": r["ordinal"],
            "source_type": r["source_type"],
            "title": r["title"],
            "url": r["url"],
            "authors": r["authors"],
            "year": r["year"],
            "verified": r["verified"],
            "excerpt": r["excerpt"],
            "accessed_at": r["accessed_at"],
        })
    return out


def get_sample(conn: sqlite3.Connection) -> Optional[Dict[str, Any]]:
    row = conn.execute("SELECT * FROM packages WHERE is_sample = 1 LIMIT 1").fetchone()
    return row_to_package(row) if row else None


# --------------------------------------------------------------------------- #
# Content hash recomputation (FR-022)
# --------------------------------------------------------------------------- #

def recompute_content_hash(conn: sqlite3.Connection, pid: str) -> Dict[str, Any]:
    """FR-022: recompute the hash from stored fields and compare."""
    pkg = get_package(conn, pid)
    if pkg is None:
        raise KeyError(pid)
    cits = get_citations(conn, pid)
    citation_objs = [
        {
            "ordinal": c["ordinal"],
            "source_type": c["source_type"],
            "title": c["title"],
            "url": c["url"],
            "authors": c["authors"],
            "year": c["year"],
            "excerpt": c["excerpt"],
            "verified": c["verified"],
            "accessed_at": c["accessed_at"],
        }
        for c in sorted(cits, key=lambda x: x["ordinal"])
    ]
    payload = {
        "question": pkg["question"],
        "findings": pkg["findings"],
        "citations": citation_objs,
        "method_notes": pkg["method_notes"],
        "score": pkg["quality_score"],
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    recomputed = "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return {
        "package_id": pid,
        "content_hash_stored": pkg["content_hash"],
        "content_hash_recomputed": recomputed,
        "match": recomputed == pkg["content_hash"],
        "signature": pkg["signature"],
        "verified_at": now_iso(),
        "verification_scope": "post_import_tamper_detection",
    }


# --------------------------------------------------------------------------- #
# API keys (FR-016, FR-017)
# --------------------------------------------------------------------------- #

def insert_api_key(conn: sqlite3.Connection, plaintext: str, label: str, config: Config, days: Optional[int] = None) -> Tuple[int, str]:
    from .auth import hash_key
    kh = hash_key(plaintext)
    prefix = plaintext[:8]
    expiry_days = days if days is not None else config.key_expiry_days
    expires = (
        _dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(days=expiry_days)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    cur = conn.execute(
        "INSERT INTO api_keys (key_hash, key_prefix, label, status, expires_at) "
        "VALUES (?,?,?,'active',?)",
        (kh, prefix, label, expires),
    )
    conn.commit()
    return int(cur.lastrowid), expires


def revoke_api_key(conn: sqlite3.Connection, ident: str) -> Dict[str, Any]:
    """Revoke by id, key prefix, or full key. Invalidates cache via caller."""
    row: Optional[sqlite3.Row] = None
    if ident.isdigit():
        row = conn.execute("SELECT * FROM api_keys WHERE id = ?", (int(ident),)).fetchone()
    else:
        from .auth import hash_key

        if ident.startswith("ak_") and len(ident) > 8:
            kh = hash_key(ident)
            row = conn.execute("SELECT * FROM api_keys WHERE key_hash = ?", (kh,)).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM api_keys WHERE key_prefix = ? AND status='active'",
                (ident,),
            ).fetchone()
    if row is None:
        raise KeyError(ident)
    conn.execute(
        "UPDATE api_keys SET status='revoked', revoked_at=? WHERE id=?",
        (now_iso(), row["id"]),
    )
    conn.commit()
    return {"id": row["id"], "key_prefix": row["key_prefix"], "status": "revoked"}


def list_api_keys(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    rows = conn.execute("SELECT * FROM api_keys ORDER BY id DESC").fetchall()
    return [
        {
            "id": r["id"],
            "key_prefix": r["key_prefix"],
            "label": r["label"],
            "status": r["status"],
            "created_at": r["created_at"],
            "expires_at": r["expires_at"],
            "revoked_at": r["revoked_at"],
        }
        for r in rows
    ]


# --------------------------------------------------------------------------- #
# Request logging (FR-014, §5.2)
# --------------------------------------------------------------------------- #

def log_request(
    conn: sqlite3.Connection,
    key_prefix: Optional[str],
    method: str,
    endpoint: str,
    status_code: int,
    response_time_ms: int,
    ip_hash: Optional[str],
    user_agent_hash: Optional[str],
    request_id: str,
) -> None:
    conn.execute(
        """INSERT INTO request_log
           (key_prefix, method, endpoint, status_code, response_time_ms,
            ip_hash, user_agent_hash, request_id)
           VALUES (?,?,?,?,?,?,?,?)""",
        (key_prefix, method, endpoint, status_code, response_time_ms, ip_hash,
         user_agent_hash, request_id),
    )
    conn.commit()


def log_ingest_run(conn: sqlite3.Connection, run_id: str, status: str, reason: Optional[str], cost_data: Optional[str]) -> None:
    conn.execute(
        "INSERT INTO ingest_runs (run_id, status, reason, cost_data) VALUES (?,?,?,?)",
        (run_id, status, reason, cost_data),
    )
    conn.commit()


def get_ingest_status(conn: sqlite3.Connection, run_id: str) -> Optional[str]:
    """Return the latest ingest_runs status for *run_id*, or None if not seen."""
    row = conn.execute(
        "SELECT status FROM ingest_runs WHERE run_id = ? ORDER BY id DESC LIMIT 1",
        (run_id,),
    ).fetchone()
    return row["status"] if row else None


# --------------------------------------------------------------------------- #
# Reporting (FR-024)
# --------------------------------------------------------------------------- #

def usage_report(conn: sqlite3.Connection, days: int = 30) -> Dict[str, Any]:
    """FR-024: aggregate request_log usage over the last *days*."""
    since = (
        _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=days)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = conn.execute(
        """SELECT key_prefix, endpoint, COUNT(*) AS c, AVG(response_time_ms) AS avg_ms
           FROM request_log
           WHERE created_at >= ? AND key_prefix IS NOT NULL
           GROUP BY key_prefix, endpoint
           ORDER BY c DESC""",
        (since,),
    ).fetchall()
    total = conn.execute(
        "SELECT COUNT(*) AS c FROM request_log WHERE created_at >= ? AND key_prefix IS NOT NULL",
        (since,),
    ).fetchone()["c"]
    by_key: Dict[str, Any] = {}
    for r in rows:
        by_key.setdefault(r["key_prefix"], {"requests": 0, "endpoints": {}})
        by_key[r["key_prefix"]]["requests"] += r["c"]
        by_key[r["key_prefix"]]["endpoints"][r["endpoint"]] = {
            "count": r["c"],
            "avg_response_ms": round(r["avg_ms"] or 0, 2),
        }
    return {
        "schema": "agentnews.usage_report/v1",
        "days": days,
        "total_authenticated_requests": total,
        "by_key": by_key,
        "generated_at": now_iso(),
    }


def costs_report(conn: sqlite3.Connection, days: int = 30) -> Dict[str, Any]:
    """FR-024: aggregate ingest_runs cost data over the last *days*."""
    since = (
        _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=days)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = conn.execute(
        """SELECT run_id, status, cost_data, created_at
           FROM ingest_runs
           WHERE created_at >= ? AND cost_data IS NOT NULL
           ORDER BY id DESC""",
        (since,),
    ).fetchall()
    total_usd = 0.0
    import_count = 0
    for r in rows:
        cost = r["cost_data"]
        if isinstance(cost, str):
            try:
                cost = json.loads(cost)
            except json.JSONDecodeError:
                continue
        if not isinstance(cost, dict):
            continue
        # Accept either "total_usd" or "cost_usd" shapes.
        total_usd += cost.get("total_usd", cost.get("cost_usd", 0.0) or 0.0)
        import_count += 1
    return {
        "schema": "agentnews.costs_report/v1",
        "days": days,
        "imports_with_cost_data": import_count,
        "total_usd": round(total_usd, 6),
        "generated_at": now_iso(),
    }


def purge_logs(conn: sqlite3.Connection, days: int = 30) -> int:
    """FR-025: delete request_log rows older than *days*. Returns rows deleted."""
    since = (
        _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=days)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    cur = conn.execute("DELETE FROM request_log WHERE created_at < ?", (since,))
    conn.commit()
    return cur.rowcount


def run_already_seen(conn: sqlite3.Connection, run_id: str) -> bool:
    """True if *run_id* appears in either packages or ingest_runs (FR-029)."""
    if get_package_by_run(conn, run_id) is not None:
        return True
    return get_ingest_status(conn, run_id) is not None
