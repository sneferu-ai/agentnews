"""HTML + Atom rendering (FR-008, FR-015, FR-031, FR-035).

A self-contained Markdown renderer and HTML sanitizer (stdlib only) implement
FR-035's allowlist because the ``markdown`` and ``bleach`` packages are not
available in every environment. The sanitizer allowlist is exactly the spec:
tags ``p h1-h6 ul ol li a strong em code pre blockquote``; attributes
``a[href] a[rel]``; external links gain ``rel="noopener noreferrer"``.
"""
from __future__ import annotations

import datetime as _dt
import html as _html
import re
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup

from .config import Config
from .ingest import ParsedCitation, ParsedRun

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
STATIC_DIR = Path(__file__).resolve().parent / "static"

ALLOWED_TAGS = {
    "p", "h1", "h2", "h3", "h4", "h5", "h6",
    "ul", "ol", "li", "a", "strong", "em", "code", "pre", "blockquote",
    "br", "hr",
}
ALLOWED_ATTRS = {"a": {"href", "rel"}}


class _Sanitizer(HTMLParser):
    """Allowlist-based HTML sanitizer (FR-035). Strips raw HTML, disallowed tags/attrs."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: List[str] = []
        self._skip_stack: List[str] = []

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        if self._skip_stack:
            return
        if tag not in ALLOWED_TAGS:
            # drop the tag; skip content only for non-void dangerous tags
            # (embed is void — no closing tag — so skip-stack would swallow siblings)
            if tag in ("script", "style", "iframe", "object"):
                self._skip_stack.append(tag)
            return
        clean_attrs: List[Tuple[str, str]] = []
        allowed = ALLOWED_ATTRS.get(tag, set())
        for name, val in attrs:
            if name not in allowed:
                continue
            if val is None:
                continue
            if name == "href":
                low = val.strip().lower()
                if low.startswith("javascript:") or low.startswith("data:"):
                    continue
            clean_attrs.append((name, val))
        if tag == "a":
            rels = {"noopener", "noreferrer"}
            for name, val in clean_attrs:
                if name == "rel":
                    for r in val.split():
                        rels.add(r.lower())
            clean_attrs = [(n, v) for n, v in clean_attrs if n != "rel"]
            clean_attrs.append(("rel", " ".join(sorted(rels))))
        attr_str = "".join(f' {n}="{_html.escape(v, quote=True)}"' for n, v in clean_attrs)
        self.out.append(f"<{tag}{attr_str}>")

    def handle_startendtag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        if self._skip_stack:
            return
        if tag in ("br", "hr") and tag in ALLOWED_TAGS:
            self.out.append(f"<{tag}>")

    def handle_endtag(self, tag: str) -> None:
        if self._skip_stack:
            if self._skip_stack and self._skip_stack[-1] == tag:
                self._skip_stack.pop()
            return
        if tag in ALLOWED_TAGS:
            self.out.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        if self._skip_stack:
            return
        self.out.append(_html.escape(data, quote=False))

    def result(self) -> str:
        return "".join(self.out)


def sanitize_html(raw: str) -> Markup:
    """Sanitize an HTML string and return a Markup object (safe to render)."""
    if not raw:
        return Markup("")
    s = _Sanitizer()
    s.feed(raw)
    s.close()
    return Markup(s.result())


# --------------------------------------------------------------------------- #
# Minimal Markdown renderer
# --------------------------------------------------------------------------- #

def _inline(text: str) -> str:
    text = _html.escape(text, quote=False)
    # code spans first to protect their content
    placeholders: List[str] = []

    def _code(m: "re.Match[str]") -> str:
        placeholders.append(f'<code>{_html.escape(m.group(1), quote=True)}</code>')
        return f"\x00{len(placeholders)-1}\x00"

    text = re.sub(r"`([^`]+)`", _code, text)
    text = re.sub(r"\*\*([^*]+?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<!\w)\*([^*\n]+?)\*(?!\w)", r"<em>\1</em>", text)
    text = re.sub(r"(?<!\w)_([^_\n]+?)_(?!\w)", r"<em>\1</em>", text)
    text = re.sub(
        r"\[([^\]]+)\]\((https?://[^\s)]+)\)",
        r'<a href="\2">\1</a>',
        text,
    )
    # restore code spans
    def _restore(m: "re.Match[str]") -> str:
        return placeholders[int(m.group(1))]

    text = re.sub(r"\x00(\d+)\x00", _restore, text)
    return text


def render_markdown(md: str) -> Markup:
    """Render a markdown string to sanitized Markup (FR-035)."""
    if not md:
        return Markup("")
    lines = md.splitlines()
    out: List[str] = []
    i = 0
    in_ul = False
    in_ol = False
    in_pre = False
    in_bq = False
    para: List[str] = []

    def close_lists() -> None:
        nonlocal in_ul, in_ol
        if in_ul:
            out.append("</ul>")
            in_ul = False
        if in_ol:
            out.append("</ol>")
            in_ol = False

    def close_bq() -> None:
        nonlocal in_bq
        if in_bq:
            out.append("</blockquote>")
            in_bq = False

    def flush_para() -> None:
        nonlocal para
        if para:
            out.append(f"<p>{_inline(' '.join(para))}</p>")
            para = []

    while i < len(lines):
        line = lines[i]
        if in_pre:
            if line.strip().startswith("```"):
                out.append("</pre>")
                in_pre = False
            else:
                out.append(_html.escape(line, quote=False) + "\n")
            i += 1
            continue
        stripped = line.strip()
        if stripped.startswith("```"):
            close_lists()
            close_bq()
            flush_para()
            out.append("<pre>")
            in_pre = True
            i += 1
            continue
        if not stripped:
            close_lists()
            close_bq()
            flush_para()
            i += 1
            continue
        m = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if m:
            close_lists()
            close_bq()
            flush_para()
            level = len(m.group(1))
            out.append(f"<h{level}>{_inline(m.group(2))}</h{level}>")
            i += 1
            continue
        if stripped.startswith("> "):
            close_lists()
            flush_para()
            if not in_bq:
                out.append("<blockquote>")
                in_bq = True
            out.append(f"<p>{_inline(stripped[2:])}</p>")
            i += 1
            continue
        else:
            close_bq()
        m_ul = re.match(r"^[-*]\s+(.*)$", stripped)
        m_ol = re.match(r"^\d+\.\s+(.*)$", stripped)
        if m_ul:
            flush_para()
            if in_ol:
                out.append("</ol>")
                in_ol = False
            if not in_ul:
                out.append("<ul>")
                in_ul = True
            out.append(f"<li>{_inline(m_ul.group(1))}</li>")
            i += 1
            continue
        if m_ol:
            flush_para()
            if in_ul:
                out.append("</ul>")
                in_ul = False
            if not in_ol:
                out.append("<ol>")
                in_ol = True
            out.append(f"<li>{_inline(m_ol.group(1))}</li>")
            i += 1
            continue
        close_lists()
        para.append(stripped)
        i += 1
    if in_pre:
        out.append("</pre>")
    close_lists()
    close_bq()
    flush_para()
    return sanitize_html("".join(out))


# --------------------------------------------------------------------------- #
# Latency disclosure (FR-015)
# --------------------------------------------------------------------------- #

def _parse_iso(ts: Optional[str]) -> Optional[_dt.datetime]:
    if not ts:
        return None
    try:
        return _dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def compute_latency(
    run_completed_at: Optional[str], published_at: Optional[str]
) -> Tuple[Optional[float], str]:
    """FR-015. Returns (latency_hours, latency_disclosure)."""
    fallback = (
        "This research package was produced by a multi-model adversarial "
        "convergence process spanning approximately 24 hours. Findings reflect "
        "the state of available sources as of the run completion timestamp."
    )
    rc = _parse_iso(run_completed_at)
    pub = _parse_iso(published_at)
    if rc is None or pub is None:
        return None, fallback
    if pub < rc:
        return None, fallback
    hours = (pub - rc).total_seconds() / 3600.0
    hours_rounded = round(hours, 1)
    disclosure = (
        "This research package was produced by a multi-model adversarial "
        "convergence process. The interval from run completion to publication "
        f"was {hours_rounded:.1f} hours. Findings reflect the state of "
        "available sources as of the run completion timestamp."
    )
    return hours_rounded, disclosure


def now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------- #
# Jinja2 environments
# --------------------------------------------------------------------------- #

def _html_env() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=select_autoescape(["html", "xml"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )


def _xml_env() -> Environment:
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=True,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    return env


# --------------------------------------------------------------------------- #
# Reuse terms (FR-011)
# --------------------------------------------------------------------------- #

REUSE_DISCLAIMER = (
    "These terms describe permitted use of the research package as synthesized "
    "by AgentNews. They do not override or supersede the intellectual property "
    "rights of any third-party sources cited within the package. Buyers are "
    "responsible for verifying the licensing status of individual cited sources "
    "before republishing excerpts."
)


def build_reuse_terms(config: Config) -> Dict[str, Any]:
    return {
        "license": "agentnews-research-v1",
        "attribution_required": True,
        "may_republish": True,
        "derivative_allowed": True,
        "commercial_allowed": True,
        "max_excerpt_words": config.excerpt_limit_words,
        "terms_version": "1.0",
        "terms_url": f"{config.public_url}/static/reuse-license-v1.md",
        "disclaimer": REUSE_DISCLAIMER,
        "generated_at": now_iso(),
    }


# --------------------------------------------------------------------------- #
# Render functions
# --------------------------------------------------------------------------- #

def _signature_summary(sig: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "model_families": list(sig.get("model_families") or []),
        "cross_vendor": sig.get("cross_vendor"),
        "degraded": bool(sig.get("degraded", False)),
    }


def render_index(
    packages: List[Dict[str, Any]],
    config: Config,
    *,
    health: Optional[Dict[str, Any]] = None,
    url_prefix: str = "",
    static_prefix: str = "",
) -> str:
    env = _html_env()
    tmpl = env.get_template("index.html")
    cards = []
    for p in packages:
        sig = p.get("signature") or {}
        fams = sig.get("model_families") or []
        cards.append({
            "id": p["id"],
            "question": p["question"],
            "published_at": p.get("published_at"),
            "score": p.get("quality_score"),
            "summary": p.get("summary") or "",
            "article_url": f"{url_prefix}/articles/{p['slug']}",
            "family_count": len(fams),
            "is_sample": p.get("is_sample"),
        })
    return tmpl.render(
        cards=cards,
        config=config,
        health=health,
        url_prefix=url_prefix,
        static_prefix=static_prefix,
        current_page="home",
        price=config.founding_price_usd,
        payment_link=config.payment_link_url,
    )


def render_article(
    pkg: Dict[str, Any],
    citations: List[Dict[str, Any]],
    config: Config,
    *,
    url_prefix: str = "",
    static_prefix: str = "",
) -> str:
    env = _html_env()
    tmpl = env.get_template("article.html")
    findings_html = render_markdown(pkg.get("findings") or "")
    method_html = render_markdown(pkg.get("method_notes") or "")
    sig = pkg.get("signature") or {}
    return tmpl.render(
        pkg=pkg,
        citations=citations,
        findings_html=findings_html,
        method_html=method_html,
        signature=sig,
        family_count=len(sig.get("model_families") or []),
        verify_url=f"{config.public_url}/v1/packages/{pkg['id']}/verify",
        license_url=f"{config.public_url}/static/reuse-license-v1.md",
        sample_url=f"{config.public_url}/v1/sample",
        catalog_url=f"{config.public_url}/v1/catalog",
        feed_url=f"{config.public_url}/feed.xml",
        access_url=f"{url_prefix}/access",
        latency_disclosure=pkg.get("latency_disclosure") or "",
        config=config,
        url_prefix=url_prefix,
        static_prefix=static_prefix,
        current_page="article",
    )


def render_access(
    config: Config,
    *,
    url_prefix: str = "",
    static_prefix: str = "",
) -> str:
    env = _html_env()
    tmpl = env.get_template("access.html")
    return tmpl.render(
        config=config,
        price=config.founding_price_usd,
        payment_link=config.payment_link_url,
        base_url=config.public_url,
        openapi_url=f"{config.public_url}/v1/openapi.json",
        sample_url=f"{config.public_url}/v1/sample",
        catalog_url=f"{config.public_url}/v1/catalog",
        feed_url=f"{config.public_url}/feed.xml",
        license_url=f"{config.public_url}/static/reuse-license-v1.md",
        url_prefix=url_prefix,
        static_prefix=static_prefix,
        current_page="access",
    )


def render_404(
    config: Config,
    *,
    url_prefix: str = "",
    static_prefix: str = "",
) -> str:
    env = _html_env()
    tmpl = env.get_template("404.html")
    return tmpl.render(
        config=config,
        url_prefix=url_prefix,
        static_prefix=static_prefix,
        current_page=None,
    )


def render_503(
    config: Config,
    *,
    url_prefix: str = "",
    static_prefix: str = "",
) -> str:
    env = _html_env()
    tmpl = env.get_template("503.html")
    return tmpl.render(
        config=config,
        url_prefix=url_prefix,
        static_prefix=static_prefix,
        current_page=None,
    )


def render_feed(packages: List[Dict[str, Any]], config: Config) -> str:
    env = _xml_env()
    tmpl = env.get_template("feed.xml")
    entries = []
    for p in packages:
        entries.append({
            "id": p["id"],
            "title": p["question"],
            "summary": p.get("summary") or "",
            "link": f"{config.public_url}/articles/{p['slug']}",
            "published": p.get("published_at") or now_iso(),
            "updated": p.get("published_at") or now_iso(),
        })
    return tmpl.render(entries=entries, config=config,
                       self_link=f"{config.public_url}/feed.xml",
                       site_link=config.public_url,
                       catalog_link=f"{config.public_url}/v1/catalog",
                       now=now_iso())
