"""Tests for the runtime-test serve script and packaging contract (FR-009,
FR-012, FR-013, FR-022, FR-035, FR-038).

These tests exercise the same path the Sneferu runtime-test phase walks:
`packaging.json` declares a start command and a `browser_smoke_plan`, and the
start command seeds the database before serving so the smoke plan can hit real
authenticated endpoints without guessing credentials or IDs.
"""
import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
RUNTIME_TEST_PACKAGE_ID = "pkg-runtime-test-0001"
RUNTIME_TEST_KEY = "ak_00000000000000000000000000000001"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _read_packaging() -> dict:
    path = REPO_ROOT / "packaging.json"
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


@pytest.fixture()
def runtime_server(tmp_path):
    """Start the runtime serve script on a throwaway DB and tear it down."""
    port = _free_port()
    db_path = tmp_path / "agentnews.db"

    env = dict(os.environ)
    env["AGENTNEWS_DB"] = str(db_path)
    env["PYTHONPATH"] = str(REPO_ROOT / "src")
    if os.environ.get("PYTHONPATH"):
        env["PYTHONPATH"] += ":" + os.environ["PYTHONPATH"]
    # Let runtime_serve.py set PUBLIC_URL to the actual local port so the
    # OpenAPI servers entry and article URLs are consistent with the harness.
    env.pop("PUBLIC_URL", None)
    # The runtime test always runs on localhost; force insecure mode so the
    # http://127.0.0.1 PUBLIC_URL passes validation.
    env["AGENTNEWS_ALLOW_INSECURE"] = "true"
    # Low limits so the rate-limit assertion stays fast.
    env["RATE_LIMIT_PER_DAY"] = "5"
    env["BURST_ALLOWANCE"] = "1"

    proc = subprocess.Popen(
        [sys.executable, str(REPO_ROOT / "scripts" / "runtime_serve.py"), str(port)],
        cwd=str(REPO_ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )

    base = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            r = httpx.get(f"{base}/healthz", timeout=1.0)
            if r.status_code == 200:
                break
        except Exception:
            pass
        time.sleep(0.2)
    else:
        try:
            out, _ = proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            out = b""
        proc.kill()
        raise RuntimeError(
            f"runtime_serve.py did not become healthy on port {port};\n{out.decode()}"
        )

    yield base

    proc.send_signal(signal.SIGTERM)
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


def test_packaging_declares_runtime_contract():
    """packaging.json declares the runtime-test command, mode, health endpoint,
    smoke plan, and install command."""
    cfg = _read_packaging()
    assert cfg.get("runtime_test_command") and "{port}" in cfg["runtime_test_command"]
    assert cfg.get("runtime_test_mode") == "http"
    assert cfg.get("health_endpoint") in {"/health", "/healthz"}
    assert cfg.get("browser_smoke_plan") == "test_plan.md"
    assert cfg.get("install_command")
    plan_path = REPO_ROOT / cfg["browser_smoke_plan"]
    assert plan_path.is_file() and not plan_path.is_symlink()


def test_runtime_serve_seeds_health_and_catalog(runtime_server):
    """The runtime server boots and serves the seeded catalog."""
    r = httpx.get(f"{runtime_server}/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["db_ok"] is True
    assert body["packages_count"] >= 1

    r = httpx.get(f"{runtime_server}/v1/catalog")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] >= 1
    assert any(p["id"] == RUNTIME_TEST_PACKAGE_ID for p in body["packages"])


def test_runtime_serve_paid_api_gated_and_readable(runtime_server):
    """Anonymous access is refused; the deterministic key unlocks the package."""
    r = httpx.get(f"{runtime_server}/v1/packages")
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "unauthorized"

    headers = {"X-API-Key": RUNTIME_TEST_KEY}
    r = httpx.get(f"{runtime_server}/v1/packages", headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert any(p["id"] == RUNTIME_TEST_PACKAGE_ID for p in body["packages"])

    r = httpx.get(f"{runtime_server}/v1/packages/{RUNTIME_TEST_PACKAGE_ID}", headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert body["schema"] == "agentnews.package/v1"
    assert len(body["citations"]) == 5
    assert body["content_hash"].startswith("sha256:")
    assert body["id"] == RUNTIME_TEST_PACKAGE_ID


def test_runtime_serve_verify_and_article(runtime_server):
    """Public verification recomputes the hash; the article renders."""
    r = httpx.get(f"{runtime_server}/v1/packages/{RUNTIME_TEST_PACKAGE_ID}/verify")
    assert r.status_code == 200
    body = r.json()
    assert body["match"] is True
    assert body["verification_scope"] == "post_import_tamper_detection"

    r = httpx.get(f"{runtime_server}/articles/{RUNTIME_TEST_PACKAGE_ID}")
    assert r.status_code == 200
    text = r.text
    assert "Convergence certificate" in text
    assert "<script" not in text
    assert f"/v1/packages/{RUNTIME_TEST_PACKAGE_ID}/verify" in text


def test_runtime_serve_rate_limit(runtime_server):
    """The deterministic key is rate-limited and the 429 carries Retry-After."""
    headers = {"X-API-Key": RUNTIME_TEST_KEY}
    for _ in range(10):
        r = httpx.get(f"{runtime_server}/v1/packages", headers=headers)
        if r.status_code == 429:
            assert r.json()["error"]["code"] == "rate_limited"
            assert int(r.headers.get("Retry-After", 0)) >= 1
            return
    pytest.fail("expected to hit 429 within 10 requests")


def test_runtime_serve_public_surface(runtime_server):
    """The runtime-seeded public surface renders without authentication."""
    # Public index (item 2)
    r = httpx.get(f"{runtime_server}/")
    assert r.status_code == 200
    assert "text/html" in r.headers.get("content-type", "")
    assert "AgentNews" in r.text
    assert '<html lang="en">' in r.text

    # Public sample (item 4)
    r = httpx.get(f"{runtime_server}/v1/sample")
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == RUNTIME_TEST_PACKAGE_ID
    assert body["question"]
    assert body.get("summary")
    assert isinstance(body["citation_previews"], list)
    assert len(body["citation_previews"]) <= 3

    # Atom feed (item 5)
    r = httpx.get(f"{runtime_server}/feed.xml")
    assert r.status_code == 200
    assert r.headers.get("content-type") == "application/atom+xml"
    assert "<feed" in r.text
    assert "Atom" in r.text

    # Access page (item 6)
    r = httpx.get(f"{runtime_server}/access")
    assert r.status_code == 200
    assert "text/html" in r.headers.get("content-type", "")
    assert "buy.example.com" in r.text

    # OpenAPI contract (item 7)
    r = httpx.get(f"{runtime_server}/v1/openapi.json")
    assert r.status_code == 200
    body = r.json()
    assert "openapi" in body
    assert "/v1/packages" in body.get("paths", {})
    assert body["servers"][0]["url"] == f"http://127.0.0.1:{runtime_server.split(':')[-1]}"


def test_runtime_serve_security_headers_and_no_script(runtime_server):
    """Security headers are present on every route and HTML carries no JS."""
    for path in ("/healthz", "/", f"/v1/catalog"):
        r = httpx.get(f"{runtime_server}{path}")
        assert r.status_code == 200
        assert r.headers.get("x-content-type-options") == "nosniff"
        assert r.headers.get("x-frame-options") == "DENY"
        assert r.headers.get("referrer-policy") == "no-referrer"
        assert "strict-transport-security" in r.headers
        csp = r.headers.get("content-security-policy", "")
        assert "script-src 'none'" in csp
        assert "'unsafe-inline'" not in csp

    # No JavaScript invariant on HTML pages (item 17)
    for path in ("/", "/access", f"/articles/{RUNTIME_TEST_PACKAGE_ID}"):
        r = httpx.get(f"{runtime_server}{path}")
        assert r.status_code == 200
        assert "<script" not in r.text


def test_runtime_serve_etag_conditional_get(runtime_server):
    """Cacheable public endpoints return ETag and honor If-None-Match."""
    r = httpx.get(f"{runtime_server}/v1/catalog")
    assert r.status_code == 200
    etag = r.headers.get("etag")
    assert etag

    r2 = httpx.get(f"{runtime_server}/v1/catalog", headers={"If-None-Match": etag})
    assert r2.status_code == 304


def test_runtime_serve_static_assets(runtime_server):
    """Static assets (CSS and reuse license) are served from the mounted dir."""
    r = httpx.get(f"{runtime_server}/static/style.css")
    assert r.status_code == 200
    assert r.headers.get("content-type") in ("text/css", "text/css; charset=utf-8")
    body = r.text
    assert "--bg" in body or "css" in body.lower()

    r = httpx.get(f"{runtime_server}/static/reuse-license-v1.md")
    assert r.status_code == 200


def test_runtime_serve_failure_paths(runtime_server):
    """Browser-facing 404s are HTML; API 404s are JSON."""
    # HTML article 404 (item 14)
    r = httpx.get(f"{runtime_server}/articles/nonexistent-slug")
    assert r.status_code == 404
    assert "text/html" in r.headers.get("content-type", "")
    assert "Page not found" in r.text
    assert "Return to the newsroom" in r.text

    # API 404 (item 11)
    r = httpx.get(
        f"{runtime_server}/v1/packages/nonexistent-id",
        headers={"X-API-Key": RUNTIME_TEST_KEY},
    )
    assert r.status_code == 404
    assert r.headers.get("content-type") == "application/json"
    assert r.json()["error"]["code"] == "not_found"
