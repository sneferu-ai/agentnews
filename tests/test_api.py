"""Tests for the FastAPI API (FR-009, FR-010, FR-012, FR-013, FR-027, FR-036,
FR-038)."""
import json

import pytest

from agentnews import auth, ingest, storage
from agentnews.config import load_config


def _seed_package(conn, good_run_dir, published=True, sample=False):
    config = load_config()
    parsed = ingest.parse_run(good_run_dir, config)
    pid = storage.insert_package(conn, parsed, config, is_sample=sample)
    if published:
        storage.transition_to_published(conn, pid, config)
    return pid


def _make_key(conn, client=None, admin_headers=None):
    """Create an API key directly via storage (key creation is CLI-only per FR-016)."""
    config = load_config()
    plaintext, _kh, _prefix = auth.generate_key()
    storage.insert_api_key(conn, plaintext, "test", config)
    return plaintext


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] in ("ok", "degraded")


def test_security_headers_present(client):
    r = client.get("/health")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
    assert "strict-transport-security" in r.headers
    assert "content-security-policy" in r.headers
    # FR-035: CSP must match spec exactly.
    csp = r.headers["content-security-policy"]
    assert "script-src 'none'" in csp
    assert "'unsafe-inline'" not in csp


def test_packages_requires_auth(client):
    r = client.get("/v1/packages")
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "unauthorized"


def test_packages_with_key(client, conn, good_run_dir):
    pid = _seed_package(conn, good_run_dir)
    key = _make_key(conn, client, {"X-Admin-Token": "test-admin-token"})
    r = client.get("/v1/packages", headers={"Authorization": f"Bearer {key}"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["schema"] == "agentnews.package_list/v1"
    assert body["count"] == 1
    assert body["packages"][0]["id"] == pid


def test_fetch_package_with_key(client, conn, good_run_dir):
    pid = _seed_package(conn, good_run_dir)
    key = _make_key(conn, client, {"X-Admin-Token": "test-admin-token"})
    r = client.get(f"/v1/packages/{pid}", headers={"Authorization": f"Bearer {key}"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["schema"] == "agentnews.package/v1"
    assert body["id"] == pid
    assert body["content_hash"].startswith("sha256:")
    assert "citations" in body and len(body["citations"]) == 5
    assert "reuse_terms" in body
    assert "signature" in body


def test_fetch_package_not_found(client, conn, good_run_dir):
    key = _make_key(conn, client, {"X-Admin-Token": "test-admin-token"})
    r = client.get("/v1/packages/nonexistent", headers={"Authorization": f"Bearer {key}"})
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"


def test_catalog_public(client, conn, good_run_dir):
    _seed_package(conn, good_run_dir)
    r = client.get("/v1/catalog")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["schema"] == "agentnews.catalog/v1"
    assert body["count"] == 1


def test_sample_public(client, conn, good_run_dir):
    _seed_package(conn, good_run_dir, sample=True)
    r = client.get("/v1/sample")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["id"]
    assert body["access_url"].endswith("/access")
    # FR-022: sample endpoint returns at most 3 redacted citation previews.
    assert len(body["citation_previews"]) <= 3


def test_sample_missing(client, conn):
    r = client.get("/v1/sample")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "no_sample"


def test_verify_endpoint_public(client, conn, good_run_dir):
    """FR-022: verify endpoint is public (no auth required)."""
    pid = _seed_package(conn, good_run_dir)
    r = client.get(f"/v1/packages/{pid}/verify")
    assert r.status_code == 200
    body = r.json()
    assert body["match"] is True


def test_verify_endpoint(client, conn, good_run_dir):
    pid = _seed_package(conn, good_run_dir)
    key = _make_key(conn, client, {"X-Admin-Token": "test-admin-token"})
    r = client.get(f"/v1/packages/{pid}/verify", headers={"Authorization": f"Bearer {key}"})
    assert r.status_code == 200
    body = r.json()
    assert body["match"] is True
    assert body["verification_scope"] == "post_import_tamper_detection"


def test_etag_returns_304(client, conn, good_run_dir):
    _seed_package(conn, good_run_dir)
    r1 = client.get("/v1/catalog")
    assert r1.status_code == 200
    etag = r1.headers.get("etag")
    assert etag
    r2 = client.get("/v1/catalog", headers={"If-None-Match": etag})
    assert r2.status_code == 304
    # Regression (finalize 2026-08-17): the 304 must not carry the 200's
    # Content-Length. uvicorn enforces body-vs-declared-length and tears down
    # the keep-alive connection on mismatch, resetting the NEXT pooled request.
    assert r2.headers.get("content-length") in (None, "0")
    assert r2.content == b""


def test_x_api_key_auth(client, conn, good_run_dir, admin_headers):
    """FR-012: X-API-Key header is the primary auth mechanism."""
    _seed_package(conn, good_run_dir)
    key = _make_key(conn, client, admin_headers)
    r = client.get("/v1/packages", headers={"X-API-Key": key})
    assert r.status_code == 200
    assert "X-RateLimit-Remaining" in r.headers
    assert int(r.headers["X-RateLimit-Remaining"]) >= 0


def test_x_api_key_preferred_over_bearer(client, conn, good_run_dir, admin_headers):
    """X-API-Key wins when both headers are present."""
    _seed_package(conn, good_run_dir)
    key = _make_key(conn, client, admin_headers)
    r = client.get("/v1/packages", headers={"X-API-Key": key, "Authorization": "Bearer invalid"})
    assert r.status_code == 200


def test_rate_limit_429_on_exhausted_key(client, conn, good_run_dir, admin_headers, strict_client):
    """AC-013: once the key's daily burst is exhausted, subsequent requests return 429."""
    _seed_package(conn, good_run_dir)
    key = _make_key(conn, client, admin_headers)
    r1 = strict_client.get("/v1/packages", headers={"X-API-Key": key})
    assert r1.status_code == 200
    r2 = strict_client.get("/v1/packages", headers={"X-API-Key": key})
    assert r2.status_code == 429
    assert r2.json()["error"]["code"] == "rate_limited"
    # FR-014: Retry-After header must be present on 429.
    assert "retry-after" in r2.headers
    assert int(r2.headers["retry-after"]) >= 1


def test_revoke_key_via_storage(client, conn, good_run_dir, admin_headers):
    """FR-016: key revocation is CLI/storage-only (no admin API endpoint)."""
    key = _make_key(conn, client, admin_headers)
    # key works
    r = client.get("/v1/packages", headers={"Authorization": f"Bearer {key}"})
    assert r.status_code == 200
    # revoke via storage (CLI path)
    storage.revoke_api_key(conn, key[:8])
    # In production the CLI and server are separate processes; the server's
    # key cache expires within 60s (auth.CACHE_TTL). In tests (same process)
    # we clear the cache to simulate that expiry.
    from agentnews.api import _state
    _state.keys.clear()
    # key now rejected
    r = client.get("/v1/packages", headers={"Authorization": f"Bearer {key}"})
    assert r.status_code == 401


def test_healthz(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert "db_ok" in body


def test_withhold_and_republish_via_storage(client, conn, good_run_dir, admin_headers):
    """FR-021: withhold/republish are CLI/storage-only (no admin API endpoint)."""
    config = load_config()
    pid = _seed_package(conn, good_run_dir)
    storage.withhold_package(conn, pid)
    # fetch now 404
    key = _make_key(conn, client, admin_headers)
    r = client.get(f"/v1/packages/{pid}", headers={"Authorization": f"Bearer {key}"})
    assert r.status_code == 404
    storage.republish_package(conn, pid, config)
    r = client.get(f"/v1/packages/{pid}", headers={"Authorization": f"Bearer {key}"})
    assert r.status_code == 200


def test_publish_via_storage(client, conn, good_run_dir, admin_headers):
    """FR-006: publish is CLI/storage-only (no admin API endpoint)."""
    config = load_config()
    parsed = ingest.parse_run(good_run_dir, config)
    pid = storage.insert_package(conn, parsed, config)  # imported, not published
    storage.transition_to_published(conn, pid, config)
    pkg = storage.get_package(conn, pid)
    assert pkg["status"] == "published"


def test_mark_sample_via_storage(client, conn, good_run_dir, admin_headers):
    """FR-022: sample set is CLI/storage-only (no admin API endpoint)."""
    pid = _seed_package(conn, good_run_dir)
    storage.mark_sample(conn, pid)
    pkg = storage.get_package(conn, pid)
    assert pkg["is_sample"] is True


def test_no_admin_endpoints_exist(client):
    """FR-016/FR-021/FR-022: admin operations must NOT be exposed via API."""
    for path in [
        "/v1/admin/keys/1/revoke",
        "/v1/admin/keys",
        "/v1/admin/packages/pkg-test-000001/withhold",
        "/v1/admin/packages/pkg-test-000001/republish",
        "/v1/admin/packages/pkg-test-000001/sample",
        "/v1/admin/packages/pkg-test-000001/publish",
    ]:
        r = client.post(path, headers={"X-Admin-Token": "test-admin-token"})
        assert r.status_code == 404, f"{path} should not exist (got {r.status_code})"
    # /v1/keys POST should also not exist
    r = client.post("/v1/keys?label=test", headers={"X-Admin-Token": "test-admin-token"})
    assert r.status_code == 404


def test_rate_limit_enforced(client, conn, good_run_dir, admin_headers):
    """FR-013: exceeding per-key limit returns 429."""
    # shrink limiter for the test
    from agentnews.api import _state
    _state.limiter.per_day = 2
    _state.limiter.burst = 0
    _seed_package(conn, good_run_dir)
    key = _make_key(conn, client, admin_headers)
    h = {"Authorization": f"Bearer {key}"}
    codes = [client.get("/v1/packages", headers=h).status_code for _ in range(4)]
    assert 429 in codes


def test_openapi_json(client):
    r = client.get("/v1/openapi.json")
    assert r.status_code == 200
    body = r.json()
    assert "openapi" in body
    assert "/v1/packages" in body["paths"]
    # FR-020: servers field must include PUBLIC_URL.
    assert "servers" in body
    assert body["servers"][0]["url"] == "https://news.example.com"


def test_index_html(client, conn, good_run_dir):
    _seed_package(conn, good_run_dir)
    r = client.get("/")
    assert r.status_code == 200
    assert "AgentNews" in r.text
    assert '<html lang="en">' in r.text


def test_article_html(client, conn, good_run_dir):
    pid = _seed_package(conn, good_run_dir)
    pkg = storage.get_package(conn, pid)
    r = client.get(f"/articles/{pkg['slug']}")
    assert r.status_code == 200
    assert pkg["question"] in r.text
    assert "<script>" not in r.text  # sanitized


def test_feed_xml(client, conn, good_run_dir):
    _seed_package(conn, good_run_dir)
    r = client.get("/feed.xml")
    assert r.status_code == 200
    assert "<feed" in r.text
    assert "Atom" in r.text


def test_access_html(client):
    r = client.get("/access")
    assert r.status_code == 200
    assert "buy.example.com" in r.text


def test_static_file(client):
    r = client.get("/static/style.css")
    assert r.status_code == 200
    assert "css" in r.text.lower() or "--bg" in r.text


def test_request_log_recorded(client, conn, good_run_dir, admin_headers):
    _seed_package(conn, good_run_dir)
    key = _make_key(conn, client, admin_headers)
    client.get("/v1/packages", headers={"Authorization": f"Bearer {key}"})
    rows = conn.execute("SELECT * FROM request_log WHERE endpoint='/v1/packages'").fetchall()
    assert len(rows) >= 1
    assert rows[0]["key_prefix"] == key[:8]
    assert rows[0]["status_code"] == 200
    assert rows[0]["ip_hash"]
    assert rows[0]["user_agent_hash"]


# --------------------------------------------------------------------------- #
# UI tests — 404/503 HTML, link visibility, skip link, CSP compliance
# --------------------------------------------------------------------------- #


def test_article_404_returns_html_with_link(client):
    """FR-036: nonexistent article returns HTML 404 with a visible link back."""
    r = client.get("/articles/nonexistent-slug")
    assert r.status_code == 404
    assert "text/html" in r.headers.get("content-type", "")
    assert "Page not found" in r.text
    assert 'href="/"' in r.text
    assert "Return to the newsroom" in r.text


def test_api_404_still_returns_json(client, conn, good_run_dir):
    """Regression guard: /v1/* 404s stay JSON, not HTML."""
    _seed_package(conn, good_run_dir)
    key = _make_key(conn)
    r = client.get("/v1/packages/nonexistent-id", headers={"Authorization": f"Bearer {key}"})
    assert r.status_code == 404
    assert r.headers["content-type"] == "application/json"
    assert r.json()["error"]["code"] == "not_found"


def test_index_has_skip_link(client, conn, good_run_dir):
    """D-1: skip link is present and points to #main."""
    _seed_package(conn, good_run_dir)
    r = client.get("/")
    assert r.status_code == 200
    assert 'class="skip-link"' in r.text
    assert 'href="#main"' in r.text
    assert 'id="main"' in r.text
    assert 'tabindex="-1"' in r.text


def test_index_has_nav_links(client, conn, good_run_dir):
    """All primary-nav links are present and visible."""
    _seed_package(conn, good_run_dir)
    r = client.get("/")
    assert r.status_code == 200
    for href in ["/access", "/feed.xml", "/v1/catalog"]:
        assert href in r.text, f"nav link {href} missing from index"


def test_index_has_offer_band(client, conn, good_run_dir):
    """Offer band is present with price and payment link."""
    _seed_package(conn, good_run_dir)
    r = client.get("/")
    assert r.status_code == 200
    assert "offer-band" in r.text
    assert "$100" in r.text
    assert "buy.example.com" in r.text


def test_article_has_breadcrumb_and_certificate(client, conn, good_run_dir):
    """Article page has breadcrumb, certificate, and reuse panel."""
    pid = _seed_package(conn, good_run_dir)
    pkg = storage.get_package(conn, pid)
    r = client.get(f"/articles/{pkg['slug']}")
    assert r.status_code == 200
    assert "breadcrumb" in r.text
    assert "Convergence certificate" in r.text
    assert "Reuse" in r.text
    assert pkg["content_hash"] in r.text


def test_article_has_verify_link(client, conn, good_run_dir):
    """Article page links to the independent verification endpoint."""
    pid = _seed_package(conn, good_run_dir)
    pkg = storage.get_package(conn, pid)
    r = client.get(f"/articles/{pkg['slug']}")
    assert r.status_code == 200
    assert f"/v1/packages/{pkg['id']}/verify" in r.text
    assert "Verify content hash" in r.text


def test_access_has_eval_links(client):
    """Access page has public evaluation links."""
    r = client.get("/access")
    assert r.status_code == 200
    for text in ["catalog", "sample", "openapi", "license"]:
        assert text in r.text.lower(), f"access page missing {text} link"


def test_no_inline_styles_in_html(client, conn, good_run_dir):
    """C-3: no inline style attributes in any rendered HTML page."""
    _seed_package(conn, good_run_dir)
    pages = ["/", "/access"]
    pkgs = storage.list_packages(conn, limit=1, offset=0, status="published")
    if pkgs:
        pages.append(f"/articles/{pkgs[0]['slug']}")
    for path in pages:
        r = client.get(path)
        assert r.status_code == 200, f"{path} returned {r.status_code}"
        assert 'style="' not in r.text, f"inline style found in {path}"


def test_no_script_tags_in_html(client, conn, good_run_dir):
    """FR-035: zero JavaScript — no script tags in any page."""
    _seed_package(conn, good_run_dir)
    pages = ["/", "/access"]
    pkgs = storage.list_packages(conn, limit=1, offset=0, status="published")
    if pkgs:
        pages.append(f"/articles/{pkgs[0]['slug']}")
    for path in pages:
        r = client.get(path)
        assert r.status_code == 200
        assert "<script" not in r.text, f"script tag found in {path}"
