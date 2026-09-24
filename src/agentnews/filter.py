"""Publishable-package structural filter (FR-002, FR-027).

URL verification runs in a **pre-transaction phase** (before any DB write) so
the write transaction itself stays <2s. The four-state ``verified`` model:

* ``1``  resolved (HTTP 2xx/3xx) — counts toward CITATION_MINIMUM
* ``-1`` restricted/bot-blocked (HTTP 401/403/429) — counts toward minimum
* ``-2`` failed (HTTP 404/5xx/timeout/DNS) — does NOT count
* ``-3`` identity mismatch — the link resolves, but to a different work than
  the citation names (checked for arXiv abstract pages, whose titles are
  machine-readable). Does NOT count. A research run that cites a real-looking
  arXiv ID for the wrong paper must not be published as "verified".
* ``0``  unchecked (default; or when --skip-verify-urls)
"""
from __future__ import annotations

import html
import re
import time
import urllib.error
import urllib.request
from typing import List, Optional, Tuple

from .config import Config
from .ingest import ParsedCitation, ParsedRun, RejectError

VERIFY_USER_AGENT = "AgentNews/0.1.0 (research-verification)"
VERIFY_PER_URL_TIMEOUT = 10


def _verify_one(url: str, timeout: int = VERIFY_PER_URL_TIMEOUT) -> int:
    """Return verified state for a single URL."""
    req = urllib.request.Request(
        url, method="GET", headers={"User-Agent": VERIFY_USER_AGENT}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            code = getattr(resp, "status", resp.getcode())
            if 200 <= code < 400:
                return 1
            if code in (401, 403, 429):
                return -1
            if code == 404 or code >= 500:
                return -2
            return -2
    except urllib.error.HTTPError as exc:
        code = exc.code
        if code in (401, 403, 429):
            return -1
        if code == 404 or code >= 500:
            return -2
        return -2
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError):
        return -2


IDENTITY_MISMATCH = -3
_ARXIV_ABS_RE = re.compile(r"^https?://(?:www\.|export\.)?arxiv\.org/abs/", re.IGNORECASE)
_META_TITLE_RE = re.compile(r'<meta\s+name="citation_title"\s+content="([^"]*)"', re.IGNORECASE)
_HTML_TITLE_RE = re.compile(r"<title>(?:\s*\[[^\]]*\]\s*)?([^<]*)</title>", re.IGNORECASE)
_TITLE_STOPWORDS = frozenset(
    "a an the of and or for to in on with from by via is are as at its".split()
)
TITLE_CONTAINMENT_MIN = 0.75


def _title_tokens(text: str) -> set:
    text = html.unescape(text or "").lower().replace("\u2019", "").replace("'", "")
    return {t for t in re.findall(r"[a-z0-9]+", text) if t not in _TITLE_STOPWORDS}


def title_matches(claimed: str, actual: str) -> bool:
    """True when the cited title's words are (almost all) in the real title.

    Containment, not equality: citations routinely shorten titles
    ("Self-Refine." for "Self-Refine: Iterative Refinement with Self-Feedback").
    """
    want = _title_tokens(claimed)
    if not want:
        return True  # nothing to compare against; resolution alone decides
    have = _title_tokens(actual)
    return len(want & have) / len(want) >= TITLE_CONTAINMENT_MIN


def _fetch_page_title(url: str, timeout: int = VERIFY_PER_URL_TIMEOUT) -> Optional[str]:
    """Title of an arXiv abstract page, or None when it cannot be read."""
    req = urllib.request.Request(url, method="GET", headers={"User-Agent": VERIFY_USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            body = resp.read(512 * 1024).decode("utf-8", errors="replace")
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError, ValueError):
        return None
    m = _META_TITLE_RE.search(body) or _HTML_TITLE_RE.search(body)
    title = html.unescape(m.group(1)).strip() if m else ""
    return title or None


def check_identity(c: ParsedCitation) -> None:
    """Downgrade a resolved arXiv citation whose page names a different work."""
    if c.verified != 1 or not c.title or not c.url or not _ARXIV_ABS_RE.match(c.url.strip()):
        return
    actual = _fetch_page_title(c.url.strip())
    if actual is not None and not title_matches(c.title, actual):
        c.verified = IDENTITY_MISMATCH


def verify_urls(
    citations: List[ParsedCitation],
    config: Config,
    skip: bool = False,
) -> None:
    """FR-002: verify citation URLs in-place. Respects total timeout cap."""
    if skip:
        for c in citations:
            c.verified = 0
        return
    deadline = time.monotonic() + config.url_verify_total_timeout
    for c in citations:
        if not c.url or not c.url.strip():
            c.verified = 0
            continue
        if time.monotonic() >= deadline:
            c.verified = 0
            continue
        c.verified = _verify_one(c.url.strip())
        if time.monotonic() < deadline:
            check_identity(c)


def _counts_toward_minimum(c: ParsedCitation, skip: bool) -> bool:
    if skip:
        return bool(c.url and c.url.strip())
    return c.verified in (1, -1)


def admit(parsed: ParsedRun, config: Config, skip_verify: bool = False) -> Tuple[bool, List[str]]:
    """Run FR-002 checks a–d. Returns (admitted, reject_codes). Mutates verified."""
    verify_urls(parsed.citations, config, skip=skip_verify)
    rejects: List[str] = []

    # (a) degraded / no signature
    if parsed.signature.get("degraded") is True:
        rejects.append("DEGRADED")

    # (b) quality score below threshold
    if parsed.quality_score < config.publishable_score_threshold:
        rejects.append(f"LOW_SCORE:{parsed.quality_score}")

    # (c) citation minimum
    qualifying = [c for c in parsed.citations if _counts_toward_minimum(c, skip_verify)]
    if len(qualifying) < config.citation_minimum:
        rejects.append(f"INSUFFICIENT_CITATIONS:{len(qualifying)}")

    # (d) excerpt overflow
    for c in parsed.citations:
        if c.excerpt:
            words = c.excerpt.split()
            if len(words) > config.excerpt_limit_words:
                rejects.append(f"EXCERPT_OVERFLOW:ordinal={c.ordinal}")
                break

    return (len(rejects) == 0, rejects)


def first_reject(rejects: List[str]) -> RejectError:
    code = rejects[0]
    if ":" in code:
        head, _, detail = code.partition(":")
        return RejectError(head, detail)
    return RejectError(code)
