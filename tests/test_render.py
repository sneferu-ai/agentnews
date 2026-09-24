"""Tests for rendering + sanitization (FR-008, FR-015, FR-035)."""
import pytest

from agentnews import render
from agentnews.config import load_config


def test_sanitize_strips_script():
    out = render.sanitize_html("<p>hi</p><script>alert(1)</script>")
    s = str(out)
    assert "<script>" not in s
    assert "alert(1)" not in s
    assert "<p>hi</p>" in s


def test_sanitize_strips_javascript_href():
    out = render.sanitize_html('<a href="javascript:alert(1)">x</a>')
    s = str(out)
    assert "javascript:" not in s


def test_sanitize_strips_disallowed_attrs():
    out = render.sanitize_html('<p class="evil" style="x:1">hi</p>')
    s = str(out)
    assert "class=" not in s
    assert "style=" not in s


def test_sanitize_external_link_gets_rel():
    out = render.sanitize_html('<a href="https://example.com">x</a>')
    s = str(out)
    assert "noopener" in s
    assert "noreferrer" in s


def test_sanitize_data_url_blocked():
    out = render.sanitize_html('<a href="data:text/html,evil">x</a>')
    s = str(out)
    assert "data:" not in s


def test_sanitize_strips_event_handlers():
    """FR-035: onclick/onerror/onload must be stripped (XSS prevention)."""
    out = render.sanitize_html('<p onclick="alert(1)" onload="evil()">hi</p>')
    s = str(out)
    assert "onclick" not in s
    assert "onload" not in s
    assert "alert" not in s
    assert "<p>hi</p>" in s


def test_sanitize_strips_iframe():
    out = render.sanitize_html('<iframe src="evil"></iframe>text')
    s = str(out)
    assert "<iframe" not in s
    assert "text" in s


def test_sanitize_strips_object_embed():
    out = render.sanitize_html('<object data="evil"></object><embed src="x">text')
    s = str(out)
    assert "<object" not in s
    assert "<embed" not in s
    assert "text" in s


def test_markdown_renders_headings_and_bold():
    out = str(render.render_markdown("# Title\n\n**bold** text"))
    assert "<h1>" in out
    assert "<strong>bold</strong>" in out


def test_markdown_renders_lists():
    out = str(render.render_markdown("- a\n- b\n\n1. one\n2. two"))
    assert "<ul>" in out and "<li>a</li>" in out
    assert "<ol>" in out and "<li>one</li>" in out


def test_markdown_renders_code_and_link():
    out = str(render.render_markdown("Use `code` and [docs](https://example.com)."))
    assert "<code>code</code>" in out
    assert '<a href="https://example.com"' in out and ">docs</a>" in out
    assert "noopener" in out  # external link gets rel attrs


def test_markdown_renders_blockquote():
    out = str(render.render_markdown("> quoted line"))
    assert "<blockquote>" in out
    assert "quoted line" in out


def test_markdown_sanitizes_raw_html():
    out = str(render.render_markdown("<script>alert(1)</script>\n\ntext"))
    assert "<script>" not in out  # script tag stripped, not executed
    assert "text" in out


def test_markdown_renders_pre_block():
    out = str(render.render_markdown("```\ncode line\n```"))
    assert "<pre>" in out
    assert "code line" in out


def test_latency_disclosure_computed():
    hours, disc = render.compute_latency(
        "2026-08-15T10:00:00Z", "2026-08-16T10:00:00Z"
    )
    assert hours == pytest.approx(24.0)
    assert "24.0 hours" in disc


def test_latency_disclosure_fallback_when_missing():
    hours, disc = render.compute_latency(None, "2026-08-16T10:00:00Z")
    assert hours is None
    assert "approximately 24 hours" in disc


def test_latency_disclosure_fallback_when_negative():
    hours, disc = render.compute_latency("2026-08-16T10:00:00Z", "2026-08-15T10:00:00Z")
    assert hours is None
    assert "approximately 24 hours" in disc


def test_render_index_html_has_meta_and_lang():
    config = load_config()
    html = render.render_index([], config)
    assert '<html lang="en">' in html
    assert 'name="viewport"' in html
    assert "AgentNews" in html


def test_render_article_html_contains_findings_and_security():
    config = load_config()
    pkg = {
        "id": "p1", "slug": "test-slug", "question": "Q?",
        "findings": "## Findings\n\nA **bold** finding.", "method_notes": None,
        "quality_score": 80.0, "content_hash": "sha256:abc",
        "reuse_terms": {"max_excerpt_words": 300},
        "signature": {"model_families": ["a", "b"], "cross_vendor": True, "degraded": False},
        "latency_disclosure": "24.0 hours elapsed",
    }
    cits = [{"ordinal": 1, "title": "T", "url": "https://example.com", "source_type": "web",
             "authors": "A", "year": "2024", "verified": 1, "excerpt": None, "accessed_at": None}]
    html = render.render_article(pkg, cits, config)
    assert '<html lang="en">' in html
    assert "bold" in html  # findings rendered
    assert "X-Content-Type-Options" not in html  # headers are middleware, not in body
    assert "agentnews-research-v1" in html
    assert "sha256:abc" in html


def test_render_feed_is_atom_xml():
    config = load_config()
    pkgs = [{
        "id": "p1", "slug": "s", "question": "Q?", "summary": "S",
        "published_at": "2026-08-16T10:00:00Z", "quality_score": 80.0,
        "signature": {"model_families": [], "degraded": False},
    }]
    xml = render.render_feed(pkgs, config)
    assert "<feed" in xml and "Atom" in xml
    assert "<entry>" in xml
    assert "Q?" in xml


def test_render_feed_empty_has_updated():
    """Empty feed must still render a valid Atom <updated> element."""
    config = load_config()
    xml = render.render_feed([], config)
    assert "<feed" in xml and "Atom" in xml
    assert "<updated>" in xml and "</updated>" in xml
    assert xml.split("<updated>")[1].split("</updated>")[0].strip()


def test_render_access_html_has_payment_link():
    config = load_config()
    html = render.render_access(config)
    assert "buy.example.com" in html
    assert "100" in html  # price
