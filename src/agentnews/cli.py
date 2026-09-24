"""AgentNews command-line interface (FR-028, §5.4).

Subcommands: init, migrate, import-run, list, show, sample set/show/unset,
withhold, republish, serve, deploy-static, key create/revoke/list, verify,
scan, watch, validate-sneferu-layout, backup, restore, report, purge-logs.

Exit codes: 0 ok, 2 ERROR (bad input), 3 REJECT (admission filter),
4 store error.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import time
from pathlib import Path
from typing import Optional

from agentnews import __version__ as agentnews_version

from . import auth, db, ingest, ratelimit, render, storage
from . import filter as filt
from .config import (
    Config,
    EnvValidationError,
    ensure_log_salt,
    load_config,
    require_admin_token,
    validate_required_env,
)
from .ingest import EXIT_BAD_INPUT, EXIT_OK, EXIT_REJECT, EXIT_STORE_ERROR, IngestError
from .render import now_iso

PROG = "agentnews"


def _print(*args, **kwargs) -> None:
    print(*args, **kwargs)


def _err(code: str, detail: str = "") -> None:
    msg = f"ERROR:{code}"
    if detail:
        msg += f":{detail}"
    _print(msg, file=sys.stderr)


def _load(env_path: str) -> Config:
    return load_config(env_path)


# --------------------------------------------------------------------------- #
# init
# --------------------------------------------------------------------------- #

def cmd_init(args: argparse.Namespace) -> int:
    env_path = args.env_file
    if not os.path.exists(env_path):
        sample = (
            "# AgentNews configuration (FR-041)\n"
            "PUBLIC_URL=https://news.example.com\n"
            "PAYMENT_LINK_URL=https://buy.example.com/agentnews\n"
            "ADMIN_TOKEN=change-me-please\n"
            "SNEFERU_RUNS_DIR=/path/to/sneferu/runs\n"
            f"LOG_SALT={hashlib.sha256(os.urandom(16)).hexdigest()[:32]}\n"
            "# AGENTNEWS_DB=./data/agentnews.db\n"
            "# AGENTNEWS_HOST=127.0.0.1\n"
            "# AGENTNEWS_PORT=8000\n"
            "# PUBLISHABLE_SCORE_THRESHOLD=60\n"
            "# RATE_LIMIT_PER_DAY=100\n"
            "# BURST_ALLOWANCE=20\n"
            "# EXCERPT_LIMIT_WORDS=300\n"
            "# CITATION_MINIMUM=3\n"
            "# URL_VERIFY_TOTAL_TIMEOUT=60\n"
            "# KEY_EXPIRY_DAYS=90\n"
            "# STATIC_OUTPUT_DIR=./static_build\n"
            "# FOUNDING_PRICE_USD=100\n"
            "# TRUST_PROXY_HEADERS=false\n"
            "# AGENTNEWS_ALLOW_INSECURE=false\n"
        )
        Path(env_path).write_text(sample, encoding="utf-8")
        _print(f"Created {env_path} — edit PUBLIC_URL/PAYMENT_LINK_URL/ADMIN_TOKEN/SNEFERU_RUNS_DIR then run `migrate`.")
    else:
        _print(f"{env_path} already exists; leaving untouched.")
    ensure_log_salt(env_path)
    # FR-041: validate required env vars
    config = _load(env_path)
    try:
        validate_required_env(config)
    except EnvValidationError as exc:
        for e in exc.errors:
            _err(e)
        return EXIT_BAD_INPUT
    if not config.log_salt:
        _err("MISSING_ENV", "LOG_SALT")
        return EXIT_BAD_INPUT
    # FR-026: init applies pending migrations
    conn = db.connect(config.db_path)
    try:
        applied = db.run_migrations(conn)
        _print(f"migrations applied: {applied or 'none (already current)'}")
    finally:
        conn.close()
    _print("init complete.")
    return EXIT_OK


# --------------------------------------------------------------------------- #
# migrate
# --------------------------------------------------------------------------- #

def cmd_migrate(args: argparse.Namespace) -> int:
    config = _load(args.env_file)
    conn = db.connect(config.db_path)
    try:
        applied = db.run_migrations(conn)
        _print(f"migrations applied: {applied or 'none (already current)'}")
        _print(f"tables present: {db.has_all_tables(conn)}")
    finally:
        conn.close()
    return EXIT_OK


# --------------------------------------------------------------------------- #
# import-run
# --------------------------------------------------------------------------- #

def cmd_import_run(args: argparse.Namespace) -> int:
    config = _load(args.env_file)
    run_dir = Path(args.run_dir).resolve()

    # FR-027: --force requires ADMIN_TOKEN
    if args.force and not config.admin_token:
        _err("ADMIN_TOKEN_REQUIRED", "import-run --force requires ADMIN_TOKEN")
        return EXIT_BAD_INPUT

    # Parse run directory (ERROR-class failures are not bypassed by --force)
    try:
        parsed = ingest.parse_run(run_dir, config)
    except IngestError as exc:
        _err(exc.code, exc.detail)
        return EXIT_BAD_INPUT

    # Run admission filter
    admitted, rejects = filt.admit(parsed, config, skip_verify=args.skip_verify_urls)

    if args.dry_run:
        # FR-001/AC-041: parse + verify + filter, write nothing.
        verdict = "ADMIT" if admitted else f"REJECT:{rejects[0] if rejects else 'UNKNOWN'}"
        _print(json.dumps({
            "run_id": parsed.run_id,
            "verdict": verdict,
            "rejects": rejects,
            "score": parsed.quality_score,
            "citations": len(parsed.citations),
            "content_hash": parsed.content_hash,
        }, indent=2))
        return EXIT_OK

    forced = False
    reject_code = ""
    if not admitted:
        if args.force:
            forced = True
            reject_code = rejects[0] if rejects else "UNKNOWN"
        else:
            exc = filt.first_reject(rejects)
            _err(exc.code, exc.detail)
            return EXIT_REJECT

    # If forced, stamp extensions.agentnews.forced_import (FR-027)
    if forced:
        if parsed.extensions is None:
            parsed.extensions = {}
        parsed.extensions.setdefault("agentnews", {})["forced_import"] = {
            "original_rejection": reject_code,
            "forced_at": now_iso(),
        }

    cost_blob = json.dumps(parsed.cost_data) if parsed.cost_data else None
    conn = db.connect(config.db_path)
    db.run_migrations(conn)
    try:
        # FR-005: idempotent re-import without --replace is a no-op
        existing = storage.get_package_by_run(conn, parsed.run_id)
        if existing is not None and not args.replace:
            storage.log_ingest_run(conn, parsed.run_id, "already_imported", None, cost_blob)
            _print(f"ALREADY_IMPORTED run_id={parsed.run_id} pid={existing['id']}")
            return EXIT_OK

        # FR-005: --replace deletes the old row, preserves the PID
        pid_override = None
        if existing is not None and args.replace:
            pid_override = existing["id"]
            storage.delete_package(conn, pid_override)

        try:
            pid = storage.insert_package(
                conn, parsed, config,
                is_sample=args.sample,
                pid_override=pid_override,
            )
        except storage.SampleLimitError as exc:
            _err("SAMPLE_LIMIT_REACHED", str(exc))
            return EXIT_BAD_INPUT
        except storage.IdCollisionError as exc:
            _err("ID_COLLISION", str(exc))
            return EXIT_STORE_ERROR

        # Determine final status per FR-006.
        if forced:
            # FR-027: forced packages are always staged, never published
            storage.transition_to_staged(conn, pid)
            storage.log_ingest_run(conn, parsed.run_id, "force_override",
                                    f"FORCE_OVERRIDE:{reject_code}", cost_blob)
            _print(f"imported pid={pid} run_id={parsed.run_id} score={parsed.quality_score} "
                   f"status=staged (forced)")
        elif args.stage:
            # FR-006: --stage forces staged with publish_after=NULL
            storage.transition_to_staged(conn, pid)
            ingest_status = "replaced" if args.replace else "imported"
            storage.log_ingest_run(conn, parsed.run_id, ingest_status, None, cost_blob)
            _print(f"imported pid={pid} run_id={parsed.run_id} score={parsed.quality_score} "
                   f"status=staged")
        elif config.staging_delay_minutes > 0:
            # FR-006: stage with publish_after for lazy publication
            publish_after = _publish_after_from_now(config.staging_delay_minutes)
            storage.transition_to_staged(conn, pid, publish_after)
            ingest_status = "replaced" if args.replace else "imported"
            storage.log_ingest_run(conn, parsed.run_id, ingest_status, None, cost_blob)
            _print(f"imported pid={pid} run_id={parsed.run_id} score={parsed.quality_score} "
                   f"status=staged publish_after={publish_after}")
        else:
            # FR-006: default (STAGING_DELAY_MINUTES=0) publishes immediately
            storage.transition_to_published(conn, pid, config)
            ingest_status = "replaced" if args.replace else "imported"
            storage.log_ingest_run(conn, parsed.run_id, ingest_status, None, cost_blob)
            _print(f"imported pid={pid} run_id={parsed.run_id} score={parsed.quality_score} "
                   f"status=published")

        if args.sample:
            storage.mark_sample(conn, pid)
        if args.show_hash:
            _print(f"content_hash={parsed.content_hash}")
        return EXIT_OK
    except sqlite3.Error as exc:
        _err("STORE_ERROR", str(exc))
        return EXIT_STORE_ERROR
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# sample / withhold / republish
# --------------------------------------------------------------------------- #

def _admin_cmd(args, fn, ok_msg) -> int:
    config = _load(args.env_file)
    try:
        require_admin_token(config)
    except EnvValidationError as exc:
        _err("ADMIN_TOKEN_REQUIRED", "; ".join(exc.errors))
        return EXIT_BAD_INPUT
    conn = db.connect(config.db_path)
    try:
        try:
            fn(conn, args.pid, config)
        except KeyError:
            _err("NOT_FOUND", args.pid)
            return EXIT_BAD_INPUT
        except (ValueError, storage.SampleLimitError) as exc:
            _err("INVALID_TRANSITION", str(exc))
            return EXIT_BAD_INPUT
        _print(ok_msg)
        return EXIT_OK
    finally:
        conn.close()


def cmd_withhold(args) -> int:
    config = _load(args.env_file)
    conn = db.connect(config.db_path)
    try:
        try:
            storage.withhold_package(conn, args.pid)
        except KeyError:
            _err("NOT_FOUND", args.pid)
            return EXIT_BAD_INPUT
        except ValueError as exc:
            _err("INVALID_TRANSITION", str(exc))
            return EXIT_BAD_INPUT
        _print(f"withheld {args.pid}")
        return EXIT_OK
    finally:
        conn.close()


def cmd_republish(args) -> int:
    config = _load(args.env_file)
    conn = db.connect(config.db_path)
    try:
        try:
            storage.republish_package(conn, args.pid, config)
        except KeyError:
            _err("NOT_FOUND", args.pid)
            return EXIT_BAD_INPUT
        except ValueError as exc:
            _err("INVALID_TRANSITION", str(exc))
            return EXIT_BAD_INPUT
        _print(f"republished {args.pid}")
        return EXIT_OK
    finally:
        conn.close()


def cmd_sample(args) -> int:
    """FR-006: sample set/unset/show (spec command form)."""
    config = _load(args.env_file)
    try:
        require_admin_token(config)
    except EnvValidationError as exc:
        _err("ADMIN_TOKEN_REQUIRED", "; ".join(exc.errors))
        return EXIT_BAD_INPUT
    conn = db.connect(config.db_path)
    try:
        action = args.sample_action
        if action == "set":
            try:
                storage.mark_sample(conn, args.pid)
            except KeyError:
                _err("NOT_FOUND", args.pid)
                return EXIT_BAD_INPUT
            except storage.SampleLimitError as exc:
                _err("SAMPLE_LIMIT_REACHED", str(exc))
                return EXIT_BAD_INPUT
            except storage.SampleRaceError as exc:
                _err("SAMPLE_RACE", str(exc))
                return EXIT_STORE_ERROR
            _print(f"marked {args.pid} as sample")
            return EXIT_OK
        if action == "unset":
            try:
                storage.unmark_sample(conn, args.pid)
            except KeyError:
                _err("NOT_FOUND", args.pid)
                return EXIT_BAD_INPUT
            _print(f"unmarked {args.pid} as sample")
            return EXIT_OK
        if action == "show":
            pkg = storage.get_sample(conn)
            if pkg is None:
                _print("no sample configured")
                return EXIT_OK
            _print(json.dumps({"id": pkg["id"], "question": pkg["question"], "run_id": pkg["run_id"]}, indent=2))
            return EXIT_OK
        _err("UNKNOWN_ACTION", action)
        return EXIT_BAD_INPUT
    finally:
        conn.close()


def cmd_report(args) -> int:
    """FR-024: report usage/costs."""
    config = _load(args.env_file)
    try:
        require_admin_token(config)
    except EnvValidationError as exc:
        _err("ADMIN_TOKEN_REQUIRED", "; ".join(exc.errors))
        return EXIT_BAD_INPUT
    conn = db.connect(config.db_path)
    try:
        days = int(args.days) if hasattr(args, "days") and args.days else 30
        if args.report_action == "usage":
            report = storage.usage_report(conn, days=days)
        elif args.report_action == "costs":
            report = storage.costs_report(conn, days=days)
        else:
            _err("UNKNOWN_REPORT", args.report_action)
            return EXIT_BAD_INPUT
        _print(json.dumps(report, indent=2))
        return EXIT_OK
    finally:
        conn.close()


def cmd_purge_logs(args) -> int:
    """FR-025: delete old request_log rows."""
    config = _load(args.env_file)
    try:
        require_admin_token(config)
    except EnvValidationError as exc:
        _err("ADMIN_TOKEN_REQUIRED", "; ".join(exc.errors))
        return EXIT_BAD_INPUT
    conn = db.connect(config.db_path)
    try:
        days = int(args.days) if hasattr(args, "days") and args.days else 30
        deleted = storage.purge_logs(conn, days=days)
        _print(f"purged {deleted} request_log rows older than {days} days")
        return EXIT_OK
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# serve
# --------------------------------------------------------------------------- #

def cmd_serve(args) -> int:
    import uvicorn

    config = _load(args.env_file)
    try:
        validate_required_env(config)
    except EnvValidationError as exc:
        for e in exc.errors:
            _err(e)
        return EXIT_BAD_INPUT
    if not config.log_salt:
        ensure_log_salt(args.env_file)
    _print(f"serving AgentNews on http://{config.host}:{config.port} (db={config.db_path})")
    uvicorn.run(
        "agentnews.api:app",
        host=config.host,
        port=int(args.port or config.port),
        reload=False,
        log_level=args.log_level,
        timeout_keep_alive=30,
        timeout_graceful_shutdown=10,
    )
    return EXIT_OK


# --------------------------------------------------------------------------- #
# deploy-static
# --------------------------------------------------------------------------- #

def cmd_deploy_static(args) -> int:
    config = _load(args.env_file)
    # FR-028/FR-041: validate required env vars before rendering
    try:
        validate_required_env(config)
    except EnvValidationError as exc:
        for e in exc.errors:
            _err(e)
        return EXIT_BAD_INPUT
    out = Path(args.output or config.static_output_dir)
    out.mkdir(parents=True, exist_ok=True)
    # FR-028: static assets live under STATIC_OUTPUT_DIR/static/
    dst_static = out / "static"
    dst_static.mkdir(parents=True, exist_ok=True)
    src_static = render.STATIC_DIR
    for p in src_static.iterdir():
        if p.is_file():
            shutil.copy2(p, dst_static / p.name)
    # Remove stale top-level static copies from older layouts
    for p in src_static.iterdir():
        if p.is_file():
            stale = out / p.name
            if stale.exists():
                stale.unlink()
    # Remove stale generated files no longer in the output layout
    for stale_name in ("feed.xml", "catalog.json"):
        stale = out / stale_name
        if stale.exists():
            stale.unlink()
    conn = db.connect(config.db_path)
    try:
        pkgs = storage.list_packages(conn, limit=10000, offset=0, status="published")
        (out / "index.html").write_text(render.render_index(pkgs, config), encoding="utf-8")
        # FR-028: articles are rendered as articles/{slug}.html
        art_dir = out / "articles"
        if art_dir.exists():
            shutil.rmtree(art_dir)
        art_dir.mkdir(parents=True, exist_ok=True)
        for pkg in pkgs:
            cits = storage.get_citations(conn, pkg["id"])
            (art_dir / f"{pkg['slug']}.html").write_text(
                render.render_article(pkg, cits, config), encoding="utf-8"
            )
        (out / "access.html").write_text(render.render_access(config), encoding="utf-8")
        (out / "404.html").write_text(render.render_404(config), encoding="utf-8")
        (out / "503.html").write_text(render.render_503(config), encoding="utf-8")
        _print(f"static site written to {out} ({len(pkgs)} packages)")
    finally:
        conn.close()
    return EXIT_OK


# --------------------------------------------------------------------------- #
# key create / revoke / list
# --------------------------------------------------------------------------- #

def cmd_key(args) -> int:
    config = _load(args.env_file)
    try:
        require_admin_token(config)
    except EnvValidationError as exc:
        _err("ADMIN_TOKEN_REQUIRED", "; ".join(exc.errors))
        return EXIT_BAD_INPUT
    conn = db.connect(config.db_path)
    db.run_migrations(conn)
    try:
        if args.key_action == "create":
            plaintext, key_hash, prefix = auth.generate_key()
            days = int(args.days) if hasattr(args, "days") and args.days else None
            rowid, expires_at = storage.insert_api_key(conn, plaintext, args.label or "subscriber", config, days=days)
            _print(f"key_id={rowid}")
            _print(f"key={plaintext}")
            _print(f"prefix={prefix}")
            _print(f"expires_at={expires_at}")
            _print("Store this key securely; it is shown only once.")
            return EXIT_OK
        if args.key_action == "revoke":
            try:
                result = storage.revoke_api_key(conn, args.ident)
            except KeyError:
                _err("NOT_FOUND", args.ident)
                return EXIT_BAD_INPUT
            _print(json.dumps(result))
            return EXIT_OK
        if args.key_action == "list":
            rows = storage.list_api_keys(conn)
            _print(json.dumps(rows, indent=2))
            return EXIT_OK
    finally:
        conn.close()
    _err("UNKNOWN_KEY_ACTION", args.key_action)
    return EXIT_BAD_INPUT


# --------------------------------------------------------------------------- #
# verify
# --------------------------------------------------------------------------- #

def cmd_verify(args) -> int:
    config = _load(args.env_file)
    conn = db.connect(config.db_path)
    try:
        try:
            result = storage.recompute_content_hash(conn, args.pid)
        except KeyError:
            _err("NOT_FOUND", args.pid)
            return EXIT_BAD_INPUT
        _print(json.dumps(result, indent=2))
        return EXIT_OK if result["match"] else 1
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# scan — LIST directories with signature.json (FR-030)
# --------------------------------------------------------------------------- #

def cmd_scan(args) -> int:
    config = _load(args.env_file)
    runs_dir = args.runs_dir or config.sneferu_runs_dir
    if not runs_dir:
        _err("MISSING_ENV", "SNEFERU_RUNS_DIR")
        return EXIT_BAD_INPUT
    runs_dir = Path(runs_dir)
    if not runs_dir.is_dir():
        _err("BAD_INPUT", f"runs dir not found: {runs_dir}")
        return EXIT_BAD_INPUT
    conn = db.connect(config.db_path)
    db.run_migrations(conn)
    try:
        found = 0
        for child in sorted(runs_dir.iterdir()):
            if not child.is_dir():
                continue
            if not (child / "signature.json").exists():
                continue
            run_id = child.name
            run_type = ingest.parse_run_type(run_id, config)
            if storage.get_package_by_run(conn, run_id) is not None:
                status = "imported"
            else:
                ingest_status = storage.get_ingest_status(conn, run_id)
                status = ingest_status if ingest_status else "not imported"
            _print(f"{child}\t{run_type}\t{status}")
            found += 1
        if found == 0:
            _print("No new runs found.")
    finally:
        conn.close()
    return EXIT_OK


# --------------------------------------------------------------------------- #
# list / show — local package inventory (FR-006, S-5)
# --------------------------------------------------------------------------- #

def cmd_list(args) -> int:
    """List all packages in the database, triggering lazy staging first."""
    config = _load(args.env_file)
    conn = db.connect(config.db_path)
    db.run_migrations(conn)
    try:
        storage.transition_staged_to_published(conn, config)
        rows = conn.execute(
            "SELECT id, status, quality_score, question FROM packages "
            "ORDER BY created_at DESC LIMIT 10000"
        ).fetchall()
        if not rows:
            _print("No packages imported.")
            return EXIT_OK
        for row in rows:
            _print(
                f"{row['id']}\t{row['status']}\tscore={row['quality_score']}\t"
                f"{row['question'][:60]}"
            )
        return EXIT_OK
    finally:
        conn.close()


def cmd_show(args) -> int:
    """Show details for a single package, triggering lazy staging first."""
    config = _load(args.env_file)
    conn = db.connect(config.db_path)
    db.run_migrations(conn)
    try:
        storage.transition_staged_to_published(conn, config)
        pkg = storage.get_package(conn, args.pid)
        if pkg is None:
            _err("NOT_FOUND", args.pid)
            return EXIT_BAD_INPUT
        _print(json.dumps({
            "id": pkg["id"],
            "status": pkg["status"],
            "question": pkg["question"],
            "summary": pkg["summary"],
            "quality_score": pkg["quality_score"],
            "run_id": pkg["run_id"],
            "run_type": pkg["run_type"],
            "published_at": pkg["published_at"],
            "latency_hours": pkg["latency_hours"],
            "latency_disclosure": pkg["latency_disclosure"],
            "is_sample": pkg["is_sample"],
        }, indent=2))
        return EXIT_OK
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# watch — auto-import new runs on an interval (FR-029)
# --------------------------------------------------------------------------- #

def _publish_after_from_now(minutes: int) -> str:
    """Return ISO-8601 UTC timestamp *minutes* from now for lazy staging."""
    now = datetime.datetime.now(datetime.timezone.utc)
    return (now + datetime.timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _finalize_import_status(
    conn: sqlite3.Connection,
    pid: str,
    config: Config,
    stage: bool = False,
) -> str:
    """Apply the FR-006 default status transition after a successful insert.

    Returns the final status string.
    """
    if stage:
        storage.transition_to_staged(conn, pid)
        return "staged"
    if config.staging_delay_minutes > 0:
        publish_after = _publish_after_from_now(config.staging_delay_minutes)
        storage.transition_to_staged(conn, pid, publish_after)
        return "staged"
    storage.transition_to_published(conn, pid, config)
    return "published"


def _acquire_watch_lock(lock_path: str) -> int:
    """Try to create the watch lockfile. Returns 0 on success, 2 if locked."""
    import errno
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        return 0
    except OSError as e:
        if e.errno == errno.EEXIST:
            _err("WATCH_LOCKED", lock_path)
            return 2
        raise


def _import_one(run_dir: Path, config, conn, skip_verify=False) -> int:
    """Import a single run directory. Returns 0 on success, 2 on reject, 4 on store error."""
    run_id = run_dir.name
    try:
        parsed = ingest.parse_run(run_dir, config)
        admitted, rejects = filt.admit(parsed, config, skip_verify=skip_verify)
        if not admitted:
            storage.log_ingest_run(conn, run_id, "rejected", ";".join(rejects), None)
            _print(f"REJECT {run_id}: {','.join(rejects)}")
            return EXIT_REJECT
        pid = storage.insert_package(conn, parsed, config)
        status = _finalize_import_status(conn, pid, config, stage=False)
        storage.log_ingest_run(conn, run_id, "imported", None, None)
        _print(f"imported {run_id} pid={pid} score={parsed.quality_score} status={status}")
        return EXIT_OK
    except IngestError as exc:
        storage.log_ingest_run(conn, run_id, "error", str(exc), None)
        _print(f"ERROR {run_id}: {exc}")
        return EXIT_BAD_INPUT
    except storage.IdCollisionError as exc:
        _err("ID_COLLISION", str(exc))
        return EXIT_STORE_ERROR
    except sqlite3.Error as exc:
        _err("STORE_ERROR", str(exc))
        return EXIT_STORE_ERROR


def cmd_watch(args) -> int:
    config = _load(args.env_file)
    runs_dir = args.runs_dir or config.sneferu_runs_dir
    if not runs_dir:
        _err("MISSING_ENV", "SNEFERU_RUNS_DIR")
        return EXIT_BAD_INPUT
    runs_dir = Path(runs_dir)
    interval = int(args.interval)
    lock_path = config.db_path + ".watch.lock"
    rc = _acquire_watch_lock(lock_path)
    if rc != 0:
        return rc

    import signal
    _stop = False

    def _sigterm(signum, frame):
        nonlocal _stop
        _stop = True

    signal.signal(signal.SIGTERM, _sigterm)
    _print(f"watching {runs_dir} (interval={interval}s); SIGTERM/Ctrl-C to stop.")
    try:
        while not _stop:
            conn = db.connect(config.db_path)
            db.run_migrations(conn)
            try:
                for child in sorted(runs_dir.iterdir()):
                    if _stop:
                        break
                    if not child.is_dir():
                        continue
                    if not (child / "signature.json").exists():
                        continue
                    run_id = child.name
                    if storage.run_already_seen(conn, run_id):
                        continue
                    result = _import_one(child, config, conn,
                                         skip_verify=args.skip_verify_urls)
                    if result == EXIT_OK and args.deploy_static_after_import:
                        _print("running deploy-static after import...")
                        ds_args = argparse.Namespace(env_file=args.env_file, output=None)
                        cmd_deploy_static(ds_args)
            finally:
                conn.close()
            if _stop:
                break
            time.sleep(interval)
    except KeyboardInterrupt:
        pass
    finally:
        try:
            os.unlink(lock_path)
        except OSError:
            pass
    _print("watch stopped.")
    return EXIT_OK


# --------------------------------------------------------------------------- #
# validate-sneferu-layout
# --------------------------------------------------------------------------- #

def cmd_validate_layout(args) -> int:
    config = _load(args.env_file)
    run_dir = Path(args.run_dir).resolve()
    try:
        report = ingest.validate_sneferu_layout(run_dir, config)
    except IngestError as exc:
        _err(exc.code, exc.detail)
        return EXIT_BAD_INPUT
    _print(json.dumps(report, indent=2))
    return EXIT_OK


# --------------------------------------------------------------------------- #
# backup / restore
# --------------------------------------------------------------------------- #

def cmd_backup(args) -> int:
    config = _load(args.env_file)
    conn = db.connect(config.db_path)
    try:
        db.wal_checkpoint(conn)
    finally:
        conn.close()
    if getattr(args, "to", None):
        import datetime as _dt
        dest_dir = Path(args.to)
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f"agentnews-{_dt.datetime.now(_dt.timezone.utc).strftime('%Y%m%d-%H%M%S')}.db"
    elif getattr(args, "output", None):
        dest = Path(args.output)
        dest.parent.mkdir(parents=True, exist_ok=True)
    else:
        _err("BAD_INPUT", "backup requires either --to <dir> or an output path")
        return EXIT_BAD_INPUT
    shutil.copy2(config.db_path, dest)
    _print(f"backed up {config.db_path} -> {dest}")
    return EXIT_OK


def cmd_restore(args) -> int:
    config = _load(args.env_file)
    src = Path(args.input)
    if not src.exists():
        _err("BAD_INPUT", f"backup not found: {src}")
        return EXIT_BAD_INPUT
    if db.server_running(config.db_path):
        _err("SERVER_RUNNING", "stop the server before restoring")
        return EXIT_BAD_INPUT
    shutil.copy2(src, config.db_path)
    _print(f"restored {src} -> {config.db_path}")
    return EXIT_OK


# --------------------------------------------------------------------------- #
# argparse wiring
# --------------------------------------------------------------------------- #

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog=PROG, description="AgentNews CLI")
    p.add_argument("--version", action="version", version=f"agentnews {agentnews_version}")
    p.add_argument("--env-file", default=".env", help="path to .env file")
    sub = p.add_subparsers(dest="command", required=False)

    # Shared parent so --env-file works on subcommands too.
    # SUPPRESS means: if not given on the subcommand, don't overwrite the
    # top-level value (or its ".env" default).
    parent = argparse.ArgumentParser(add_help=False)
    parent.add_argument("--env-file", default=argparse.SUPPRESS, help="path to .env file")

    sub.add_parser("init", parents=[parent], help="create .env template and generate LOG_SALT").set_defaults(func=cmd_init)

    sub.add_parser("migrate", parents=[parent], help="run pending DB migrations").set_defaults(func=cmd_migrate)

    ip = sub.add_parser("import-run", parents=[parent], help="import a single Sneferu run directory")
    ip.add_argument("run_dir")
    ip.add_argument("--stage", action="store_true", help="land as staged (FR-006)")
    ip.add_argument("--sample", action="store_true")
    ip.add_argument("--skip-verify-urls", action="store_true")
    ip.add_argument("--dry-run", action="store_true", help="parse + verify + filter, write nothing (AC-041)")
    ip.add_argument("--force", action="store_true", help="bypass REJECT, requires ADMIN_TOKEN (FR-027)")
    ip.add_argument("--replace", action="store_true", help="delete + re-import preserving PID (FR-005)")
    ip.add_argument("--show-hash", action="store_true")
    ip.set_defaults(func=cmd_import_run)

    # Spec command form: `sample set <id>` (FR-006, AC-022).
    sa = sub.add_parser("sample", parents=[parent], help="manage the free sample package")
    sasub = sa.add_subparsers(dest="sample_action", required=True)
    sas = sasub.add_parser("set", parents=[parent], help="mark a package as the single sample")
    sas.add_argument("pid")
    sasub.add_parser("show", parents=[parent], help="show the current sample package")
    sau = sasub.add_parser("unset", parents=[parent], help="remove the sample flag from a package")
    sau.add_argument("pid")
    sa.set_defaults(func=cmd_sample)

    wh = sub.add_parser("withhold", parents=[parent], help="withhold a published package")
    wh.add_argument("pid")
    wh.set_defaults(func=cmd_withhold)

    rp = sub.add_parser("republish", parents=[parent], help="republish a withheld package")
    rp.add_argument("pid")
    rp.set_defaults(func=cmd_republish)

    sv = sub.add_parser("serve", parents=[parent], help="run the ASGI server")
    sv.add_argument("--port", default=None)
    sv.add_argument("--log-level", default="info")
    sv.set_defaults(func=cmd_serve)

    ds = sub.add_parser("deploy-static", parents=[parent], help="write a static site to a directory")
    ds.add_argument("--output", default=None)
    ds.set_defaults(func=cmd_deploy_static)

    k = sub.add_parser("key", parents=[parent], help="manage API keys")
    ksub = k.add_subparsers(dest="key_action", required=True)
    kc = ksub.add_parser("create", parents=[parent])
    kc.add_argument("--label", default="")
    kc.add_argument("--days", default=None, help="key validity in days (default: KEY_EXPIRY_DAYS env var)")
    kr = ksub.add_parser("revoke", parents=[parent])
    kr.add_argument("ident")
    ksub.add_parser("list", parents=[parent])
    k.set_defaults(func=cmd_key)

    v = sub.add_parser("verify", parents=[parent], help="recompute and compare content hash")
    v.add_argument("pid")
    v.set_defaults(func=cmd_verify)

    sc = sub.add_parser("scan", parents=[parent], help="list Sneferu run dirs and their import status")
    sc.add_argument("--runs-dir", default=None)
    sc.set_defaults(func=cmd_scan)

    sub.add_parser("list", parents=[parent], help="list all packages in the database").set_defaults(func=cmd_list)

    sh = sub.add_parser("show", parents=[parent], help="show details for a package")
    sh.add_argument("pid")
    sh.set_defaults(func=cmd_show)

    w = sub.add_parser("watch", parents=[parent], help="auto-import new runs on an interval")
    w.add_argument("--runs-dir", "--dir", dest="runs_dir", default=None, help="directory of Sneferu runs (default: SNEFERU_RUNS_DIR)")
    w.add_argument("--interval", default="60", help="poll interval in seconds")
    w.add_argument("--skip-verify-urls", action="store_true")
    w.add_argument("--deploy-static-after-import", action="store_true", help="run deploy-static after each import")
    w.set_defaults(func=cmd_watch)

    vl = sub.add_parser("validate-sneferu-layout", parents=[parent], help="compatibility report for a run dir")
    vl.add_argument("run_dir")
    vl.set_defaults(func=cmd_validate_layout)

    bp = sub.add_parser("backup", parents=[parent], help="checkpoint WAL and copy the DB")
    bp.add_argument("output", nargs="?", default=None, help="full destination path (optional; use --to for a timestamped file in a directory)")
    bp.add_argument("--to", dest="to", default=None, help="destination directory; writes agentnews-YYYYMMDD-HHMMSS.db")
    bp.set_defaults(func=cmd_backup)

    rs = sub.add_parser("restore", parents=[parent], help="restore DB from a backup (server must be stopped)")
    rs.add_argument("input")
    rs.set_defaults(func=cmd_restore)

    # FR-024: reporting subcommands.
    rp2 = sub.add_parser("report", parents=[parent], help="usage and cost reports")
    rpsub = rp2.add_subparsers(dest="report_action", required=True)
    rpu = rpsub.add_parser("usage", parents=[parent], help="request-log usage by key and endpoint")
    rpu.add_argument("--days", default="30", help="lookback window in days")
    rpc = rpsub.add_parser("costs", parents=[parent], help="imported-run cost totals")
    rpc.add_argument("--days", default="30", help="lookback window in days")
    rp2.set_defaults(func=cmd_report)

    # FR-025: log retention.
    pl = sub.add_parser("purge-logs", parents=[parent], help="delete request_log rows older than N days")
    pl.add_argument("--days", default="30", help="retention window in days")
    pl.set_defaults(func=cmd_purge_logs)

    return p


def main(argv: Optional[list] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        # --version already exited; reaching here means no subcommand was given.
        parser.print_help()
        return EXIT_BAD_INPUT
    if not hasattr(args, "env_file") or args.env_file is None:
        args.env_file = ".env"
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
