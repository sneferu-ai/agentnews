"""Tests for the publishable-package filter (FR-002) and URL verification."""
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from agentnews import filter as filt
from agentnews import ingest
from agentnews.config import load_config


def _make_parsed(question="Q", findings="F", score=80.0, citations=None, degraded=False):
    cits = citations or []
    return ingest.ParsedRun(
        run_id="r1", run_type="research", question=question, summary=None,
        findings=findings, method_notes=None, citations=cits,
        signature={"degraded": degraded, "model_families": ["a", "b"]},
        quality_score=score,
    )


def test_admit_passes_with_skip_verify():
    config = load_config()
    cits = [ingest.ParsedCitation(ordinal=i, url=f"https://example.com/{i}") for i in range(1, 4)]
    parsed = _make_parsed(score=80.0, citations=cits)
    ok, rejects = filt.admit(parsed, config, skip_verify=True)
    assert ok, rejects
    assert all(c.verified == 0 for c in parsed.citations)


def test_admit_rejects_degraded():
    config = load_config()
    cits = [ingest.ParsedCitation(ordinal=i, url=f"https://example.com/{i}") for i in range(1, 4)]
    parsed = _make_parsed(score=80.0, citations=cits, degraded=True)
    ok, rejects = filt.admit(parsed, config, skip_verify=True)
    assert not ok
    assert "DEGRADED" in rejects


def test_admit_rejects_low_score():
    config = load_config()
    cits = [ingest.ParsedCitation(ordinal=i, url=f"https://example.com/{i}") for i in range(1, 4)]
    parsed = _make_parsed(score=40.0, citations=cits)
    ok, rejects = filt.admit(parsed, config, skip_verify=True)
    assert not ok
    assert any(r.startswith("LOW_SCORE") for r in rejects)


def test_admit_rejects_insufficient_citations():
    config = load_config()
    cits = [ingest.ParsedCitation(ordinal=1, url="https://example.com/1")]
    parsed = _make_parsed(score=80.0, citations=cits)
    ok, rejects = filt.admit(parsed, config, skip_verify=True)
    assert not ok
    assert any(r.startswith("INSUFFICIENT_CITATIONS") for r in rejects)


def test_admit_rejects_excerpt_overflow():
    config = load_config()
    big = " ".join(["word"] * (config.excerpt_limit_words + 10))
    cits = [
        ingest.ParsedCitation(ordinal=i, url=f"https://example.com/{i}", excerpt=big if i == 1 else None)
        for i in range(1, 4)
    ]
    parsed = _make_parsed(score=80.0, citations=cits)
    ok, rejects = filt.admit(parsed, config, skip_verify=True)
    assert not ok
    assert any(r.startswith("EXCERPT_OVERFLOW") for r in rejects)


def test_admit_first_reject():
    rejects = ["LOW_SCORE:40", "DEGRADED"]
    err = filt.first_reject(rejects)
    assert err.code == "LOW_SCORE"
    assert err.detail == "40"


def _start_local_server(status_code):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(status_code)
            self.end_headers()
        def log_message(self, *a, **k):
            pass
    srv = HTTPServer(("127.0.0.1", 0), Handler)
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv, port


def test_verify_one_resolves():
    srv, port = _start_local_server(200)
    try:
        assert filt._verify_one(f"http://127.0.0.1:{port}/ok") == 1
    finally:
        srv.shutdown()


def test_verify_one_restricted():
    srv, port = _start_local_server(403)
    try:
        assert filt._verify_one(f"http://127.0.0.1:{port}/denied") == -1
    finally:
        srv.shutdown()


def test_verify_one_failed_404():
    srv, port = _start_local_server(404)
    try:
        assert filt._verify_one(f"http://127.0.0.1:{port}/missing") == -2
    finally:
        srv.shutdown()


def test_verify_one_failed_dns():
    assert filt._verify_one("http://nonexistent.invalid." ) == -2


def test_admit_counts_restricted_toward_minimum():
    """FR-002: restricted (401/403/429) URLs count toward CITATION_MINIMUM."""
    config = load_config()
    srv, port = _start_local_server(403)
    try:
        cits = [ingest.ParsedCitation(ordinal=i, url=f"http://127.0.0.1:{port}/{i}") for i in range(1, 4)]
        parsed = _make_parsed(score=80.0, citations=cits)
        ok, rejects = filt.admit(parsed, config, skip_verify=False)
        assert ok, rejects
        assert all(c.verified == -1 for c in parsed.citations)
    finally:
        srv.shutdown()


def test_admit_does_not_count_failed_toward_minimum():
    config = load_config()
    srv, port = _start_local_server(500)
    try:
        cits = [ingest.ParsedCitation(ordinal=i, url=f"http://127.0.0.1:{port}/{i}") for i in range(1, 4)]
        parsed = _make_parsed(score=80.0, citations=cits)
        ok, rejects = filt.admit(parsed, config, skip_verify=False)
        assert not ok
        assert any(r.startswith("INSUFFICIENT_CITATIONS") for r in rejects)
    finally:
        srv.shutdown()
