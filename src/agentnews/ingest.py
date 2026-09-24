"""Sneferu run directory parser (FR-001, FR-003, FR-004, §5.3.1).

Reads a completed Sneferu run directory read-only (FR-023) and produces a
``ParsedRun`` with all package fields, the quality score, the content hash,
and the nested ``extensions`` object.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .config import Config

# Exit codes (S-5)
EXIT_OK = 0
EXIT_BAD_INPUT = 2
EXIT_REJECT = 3
EXIT_STORE_ERROR = 4


class IngestError(Exception):
    """ERROR-class input failure (exit 2). Carries a machine-greppable line."""

    def __init__(self, code: str, detail: str = ""):
        self.code = code
        self.detail = detail
        msg = f"ERROR:{code}"
        if detail:
            msg += f":{detail}"
        super().__init__(msg)


class RejectError(Exception):
    """REJECT-class admission failure (exit 3). Not bypassable except --force."""

    def __init__(self, code: str, detail: str = ""):
        self.code = code
        self.detail = detail
        msg = f"REJECT:{code}"
        if detail:
            msg += f":{detail}"
        super().__init__(msg)


@dataclass
class ParsedCitation:
    ordinal: int
    source_type: Optional[str] = None
    title: Optional[str] = None
    url: Optional[str] = None
    authors: Optional[str] = None
    year: Optional[str] = None
    excerpt: Optional[str] = None
    verified: int = 0
    accessed_at: Optional[str] = None

    def canonical(self) -> Dict[str, Any]:
        return {
            "ordinal": self.ordinal,
            "source_type": self.source_type,
            "title": self.title,
            "url": self.url,
            "authors": self.authors,
            "year": self.year,
            "excerpt": self.excerpt,
            "verified": self.verified,
            "accessed_at": self.accessed_at,
        }


@dataclass
class ParsedRun:
    run_id: str
    run_type: str
    question: str
    summary: Optional[str]
    findings: str
    method_notes: Optional[str]
    citations: List[ParsedCitation] = field(default_factory=list)
    signature: Dict[str, Any] = field(default_factory=dict)
    quality_score: float = 0.0
    cost_data: Optional[Dict[str, Any]] = None
    extensions: Dict[str, Any] = field(default_factory=dict)
    run_started_at: Optional[str] = None
    run_completed_at: Optional[str] = None
    content_hash: str = ""


REQUIRED_FILES = ("signature.json", "builder_packet/artifact.md", "problem_statement.md")
OPTIONAL_FILES = ("confidence_map.json", "cost.json", "corpus_manifest.json", "claim_lens.json")
SIG_REQUIRED_KEYS = ("degraded", "model_families")

# Real Sneferu signature.json (schema_version 1.x) does not carry the flat
# ``degraded`` / ``model_families`` keys the admission screen reads: degradation
# lives under ``degradation`` / ``signature_class`` and the models under
# ``cast``. Without this projection no real Sneferu run could be imported —
# only the hand-written fixtures could. The projection is also what gets
# stored and served, so the raw signature's internal run configuration and
# per-role model parameters never reach subscribers.
_LINEAGE_RULES = (
    ("claude", "anthropic"), ("opus", "anthropic"), ("sonnet", "anthropic"),
    ("haiku", "anthropic"), ("gpt", "openai"), ("codex", "openai"),
    ("gemini", "google"), ("llama", "meta"), ("muse", "meta"),
    ("glm", "zhipu"), ("kimi", "moonshot"), ("deepseek", "deepseek"),
    ("qwen", "alibaba"), ("minimax", "minimax"), ("nemotron", "nvidia"),
    ("mistral", "mistral"),
)
_PROVIDER_LINEAGE = {
    "anthropic_api": "anthropic", "claude_code_cli": "anthropic", "cowork": "anthropic",
    "openai_api": "openai", "openai_responses": "openai", "codex_cli": "openai",
    "gpt_desktop": "openai", "chatgpt": "openai", "kimi": "moonshot",
    "kimi_cli": "moonshot", "minimax": "minimax",
}


def _model_lineage(provider: Any, model: Any) -> Optional[str]:
    slug = str(model or "").strip().lower()
    for needle, lineage in _LINEAGE_RULES:
        if needle in slug:
            return lineage
    prov = str(provider or "").strip().lower()
    if prov in _PROVIDER_LINEAGE:
        return _PROVIDER_LINEAGE[prov]
    if slug:
        return slug.rsplit("/", 1)[-1]  # unknown trainer: name the model itself
    return None


def _mean_scorer_axes(convergence: Any) -> Optional[float]:
    if not isinstance(convergence, dict):
        return None
    final_scores = convergence.get("final_scores")
    if not isinstance(final_scores, dict):
        return None
    vals: List[float] = []
    for role, axes in final_scores.items():
        if not str(role).startswith("scorer") or not isinstance(axes, dict):
            continue
        for v in axes.values():
            if isinstance(v, (int, float)) and not isinstance(v, bool) and 0 <= v <= 100:
                vals.append(float(v))
    return round(sum(vals) / len(vals), 2) if vals else None


def _dispatched_models(cost_data: Any) -> List[str]:
    """Models that actually ran, from cost.json ``by_model`` (empty if unknown)."""
    if isinstance(cost_data, dict) and isinstance(cost_data.get("by_model"), dict):
        return [str(m) for m in cost_data["by_model"] if str(m).strip()]
    return []


def normalize_signature(signature: Dict[str, Any], cost_data: Any = None) -> Dict[str, Any]:
    """Return the signature in the shape the rest of AgentNews reads.

    The flat shape passes through unchanged. A real Sneferu signature (it has
    ``cast`` and ``degradation`` objects) is projected onto the flat keys; any
    other shape is returned untouched so the required-key check names what is
    missing. ``model_families`` comes from the models that actually ran
    (cost.json ``by_model``) when that is known — a configured cast seat that
    never dispatched is not an independent trainer of this bundle — and from
    the configured cast otherwise.
    """
    if all(k in signature for k in SIG_REQUIRED_KEYS):
        return signature
    cast = signature.get("cast")
    degradation = signature.get("degradation")
    if not isinstance(cast, dict) or not isinstance(degradation, dict):
        return signature
    sig_class = signature.get("signature_class")
    sig_class = sig_class if isinstance(sig_class, dict) else {}
    seats = [seat for seat in cast.values() if isinstance(seat, dict)]
    provider_of = {str(seat.get("model")): seat.get("provider") for seat in seats}
    ran = _dispatched_models(cost_data)
    pairs = [(provider_of.get(m), m) for m in ran] if ran else [
        (seat.get("provider"), seat.get("model")) for seat in seats
    ]
    families = sorted({lin for prov, model in pairs for lin in [_model_lineage(prov, model)] if lin})
    distinct = sig_class.get("distinct_trainers")
    cross_vendor: Optional[bool]
    if sig_class.get("unverified") is True:
        cross_vendor = None  # Sneferu could not verify every role's model: claim nothing
    elif isinstance(distinct, int) and not isinstance(distinct, bool):
        cross_vendor = distinct >= 2
    else:
        cross_vendor = len(families) >= 2
    convergence = signature.get("convergence")
    projected: Dict[str, Any] = {
        "degraded": bool(degradation.get("degraded")) or bool(sig_class.get("degraded")),
        "model_families": families,
        "cross_vendor": cross_vendor,
        "source_schema": "sneferu-signature",
        "model_families_from": "dispatched_models" if ran else "configured_cast",
        "sneferu_schema_version": signature.get("schema_version"),
    }
    score = _mean_scorer_axes(convergence)
    if score is not None:
        projected["convergence_score"] = score
    if isinstance(signature.get("signed_at"), str):
        projected["converged_at"] = signature["signed_at"]
    if isinstance(sig_class.get("signature_class"), str):
        projected["signature_class"] = sig_class["signature_class"]
    if isinstance(convergence, dict) and isinstance(convergence.get("adversary_verdict_class"), str):
        projected["adversary_verdict_class"] = convergence["adversary_verdict_class"]
    grade = signature.get("run_grade")
    if isinstance(grade, dict):
        projected["run_grade"] = {
            k: grade[k] for k in ("controller_integrity", "artifact_quality") if isinstance(grade.get(k), str)
        }
    return projected


# --------------------------------------------------------------------------- #
# File reading
# --------------------------------------------------------------------------- #

def _read_file(path: Path, name: str) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise IngestError("MISSING_FILE", name)
    except OSError as exc:
        raise IngestError("BAD_INPUT", f"cannot read {name}: {exc}")


def _read_json(path: Path, name: str) -> Any:
    text = _read_file(path, name)
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise IngestError("MALFORMED_INPUT", f"{name}:{exc.msg}")


def _find_signature(run_dir: Path) -> Path:
    """FR-001: top-level signature.json wins; subdirectory files ignored+warned."""
    top = run_dir / "signature.json"
    if top.exists():
        return top
    # search subdirectories but warn
    others = list(run_dir.rglob("signature.json"))
    if others:
        warnings.warn(f"top-level signature.json missing; using {others[0]}")
        return others[0]
    raise IngestError("MISSING_FILE", "signature.json")


# --------------------------------------------------------------------------- #
# Quality score (FR-003)
# --------------------------------------------------------------------------- #

def _collect_numeric_leaves(obj: Any) -> List[float]:
    out: List[float] = []
    if isinstance(obj, bool):
        return out  # bool is int subclass; FR-003 says numeric leaf values
    if isinstance(obj, (int, float)):
        out.append(float(obj))
    elif isinstance(obj, dict):
        for v in obj.values():
            out.extend(_collect_numeric_leaves(v))
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            out.extend(_collect_numeric_leaves(v))
    return out


def compute_quality_score(confidence_map: Any, signature: Dict[str, Any]) -> float:
    """FR-003 ordered fallback chain."""
    if isinstance(confidence_map, dict):
        for key in ("score", "quality_score"):
            val = confidence_map.get(key)
            if isinstance(val, (int, float)) and not isinstance(val, bool):
                if 0 <= val <= 100:
                    return float(val)
        oc = confidence_map.get("overall_confidence")
        if isinstance(oc, (int, float)) and not isinstance(oc, bool):
            if 0 <= oc <= 1:
                return float(oc) * 100.0
        leaves = _collect_numeric_leaves(confidence_map)
        if leaves:
            mean = sum(leaves) / len(leaves)
            if 0 <= mean <= 1:
                return mean * 100.0
            if 0 <= mean <= 100:
                return float(mean)
    conv = signature.get("convergence_score")
    if isinstance(conv, (int, float)) and not isinstance(conv, bool):
        if 0 <= conv <= 100:
            return float(conv)
    return 0.0


# --------------------------------------------------------------------------- #
# Markdown section parsing
# --------------------------------------------------------------------------- #

_H2_RE = re.compile(r"^##\s+(.*?)\s*$", re.MULTILINE)


def _find_section(text: str, predicate) -> Tuple[Optional[str], int]:
    """Return (heading_match_text, start_offset_of_body) or (None, -1)."""
    for m in _H2_RE.finditer(text):
        heading = m.group(1).strip()
        if predicate(heading):
            body_start = m.end()
            return heading, body_start
    return None, -1


def _section_body(text: str, body_start: int) -> str:
    """Text from body_start until the next ``## `` heading (or EOF)."""
    rest = text[body_start:]
    next_h2 = _H2_RE.search(rest)
    if next_h2:
        return rest[: next_h2.start()].strip()
    return rest.strip()


def _find_references_section(text: str) -> Optional[str]:
    heading, body_start = _find_section(text, lambda h: "reference" in h.lower())
    if heading is None:
        return None
    return _section_body(text, body_start)


def _find_findings_section(text: str) -> Tuple[Optional[str], str]:
    def is_findings(h: str) -> bool:
        hl = h.lower()
        return hl in ("findings", "results", "analysis") or hl.startswith("findings")

    heading, body_start = _find_section(text, is_findings)
    if heading is None:
        return None, ""
    return heading, _section_body(text, body_start)


def _find_methodology_section(text: str) -> Optional[str]:
    def is_method(h: str) -> bool:
        hl = h.lower().strip()
        return hl in ("methodology", "method") or hl.startswith("methodology")

    heading, body_start = _find_section(text, is_method)
    if heading is None:
        return None
    return _section_body(text, body_start)


def _strip_frontmatter(text: str) -> Tuple[Optional[Dict[str, str]], str]:
    """Parse simple YAML-ish frontmatter delimited by ``---`` lines."""
    if not text.startswith("---"):
        return None, text
    end = text.find("\n---", 3)
    if end == -1:
        return None, text
    block = text[3:end].strip()
    body = text[end + 4:].lstrip("\n")
    meta: Dict[str, str] = {}
    for line in block.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" in line:
            key, _, val = line.partition(":")
            val = val.strip()
            if (len(val) >= 2 and val[0] in "\"'" and val[-1] == val[0]):
                val = val[1:-1]
            meta[key.strip()] = val
    return meta, body


def extract_question(problem_text: str, artifact_meta: Optional[Dict[str, str]]) -> str:
    """First non-empty paragraph or first # / ## heading; strip leading #."""
    for line in problem_text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip()
        return stripped
    if artifact_meta and artifact_meta.get("title"):
        return artifact_meta["title"].strip()
    raise RejectError("EMPTY_ARTIFACT")


def extract_summary(findings: str, artifact_meta: Optional[Dict[str, str]]) -> Optional[str]:
    if artifact_meta:
        for key in ("summary", "description"):
            val = artifact_meta.get(key)
            if val:
                return val.strip()
    if not findings:
        return None
    # first paragraph: text before first blank line or ## heading
    para = []
    for line in findings.splitlines():
        if line.startswith("## "):
            break
        if line.strip() == "" and para:
            break
        para.append(line)
    text = "\n".join(para).strip()
    if text:
        return text
    # fallback: first 280 chars at word boundary
    flat = " ".join(findings.split())
    if len(flat) <= 280:
        return flat
    cut = flat.rfind(" ", 0, 280)
    if cut == -1:
        cut = 280
    return flat[:cut].rstrip() + "…"


# --------------------------------------------------------------------------- #
# Citation parsing (§5.3.1)
# --------------------------------------------------------------------------- #

_URL_RE = re.compile(r"https?://[^\s)>\]]+")
_YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")
_ORDINAL_BRACKET_RE = re.compile(r"^\[\s*(\d+)\s*\]")
_ORDINAL_DOT_RE = re.compile(r"^\s*(\d+)\s*\.")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^\s)]+)\)")


def _infer_source_type(url: Optional[str]) -> str:
    if not url:
        return "other"
    low = url.lower()
    if "doi.org" in low:
        return "doi"
    if "arxiv.org" in low:
        return "arxiv"
    return "web"


def _find_authors(line: str) -> Optional[str]:
    """Last parenthesized group containing a comma or the word 'and'.

    Strips a trailing 4-digit year (optionally preceded by a comma) so that
    ``(Smith, Lee, 2024)`` yields authors ``Smith, Lee`` and not
    ``Smith, Lee, 2024``.
    """
    groups = re.findall(r"\(([^()]*)\)", line)
    for g in reversed(groups):
        gl = g.lower()
        if "," in g or " and " in gl:
            result = g.strip()
            result = re.sub(r",?\s*(?:19|20)\d{2}\s*$", "", result).strip()
            return result if result else None
    return None


def _find_year(line: str) -> Optional[str]:
    m = _YEAR_RE.search(line)
    return m.group(0) if m else None


def _strip_md(text: str) -> str:
    text = _BOLD_RE.sub(r"\1", text)
    text = re.sub(r"\*([^*]+)\*", r"\1", text)
    text = re.sub(r"_([^_]+)_", r"\1", text)
    return text.strip()


def _parse_citation_line(line: str) -> Optional[ParsedCitation]:
    line = line.strip()
    if not line:
        return None
    ordinal: Optional[int] = None
    m = _ORDINAL_BRACKET_RE.match(line)
    rest = line
    if m:
        ordinal = int(m.group(1))
        rest = line[m.end():].strip()
    else:
        m = _ORDINAL_DOT_RE.match(line)
        if m:
            ordinal = int(m.group(1))
            rest = line[m.end():].strip()
    if ordinal is None:
        return None

    url_match = _URL_RE.search(rest)
    url = url_match.group(0) if url_match else None

    # title extraction
    title: Optional[str] = None
    bold = _BOLD_RE.search(rest)
    if bold:
        title = _strip_md(bold.group(1))
    else:
        link = _LINK_RE.search(rest)
        if link:
            title = _strip_md(link.group(1))
            if url is None:
                url = link.group(2)
        else:
            # text before — . or (
            cut = len(rest)
            for sep in (" — ", " - ", ". ", " ("):
                idx = rest.find(sep)
                if idx != -1 and idx < cut:
                    cut = idx
            candidate = rest[:cut].strip()
            if candidate:
                title = _strip_md(candidate)

    authors = _find_authors(rest)
    year = _find_year(rest)
    source_type = _infer_source_type(url)
    return ParsedCitation(
        ordinal=ordinal,
        source_type=source_type,
        title=title,
        url=url,
        authors=authors,
        year=year,
    )


# Sneferu research artifacts write their reference lists as unnumbered bullets
# keyed by an identifier, e.g.
#   - `arXiv:2305.04388` – Turpin et al. (2023). Language Models Don't Always Say What They Think.
# The ordinal-only parser above skips every such line, which rejected real runs
# with INSUFFICIENT_CITATIONS:0. Bullets get the next free ordinal, and a bare
# arXiv / DOI identifier becomes its canonical resolvable URL.
_BULLET_RE = re.compile(r"^\s*[-*+]\s+(.*\S)\s*$")
_ARXIV_ID_RE = re.compile(r"\barXiv:\s*(\d{4}\.\d{4,5}(?:v\d+)?|[a-z\-]+(?:\.[A-Z]{2})?/\d{7})", re.IGNORECASE)
_DOI_ID_RE = re.compile(r"\bdoi:\s*(10\.\d{4,9}/[^\s`<>]+)", re.IGNORECASE)
_LEADING_ID_RE = re.compile(r"^`[^`]*`\s*[\u2013\u2014:-]?\s*")
_APA_RE = re.compile(r"^(?P<authors>[^()]+?)\s*\((?P<year>\d{4})[a-z]?\)\.?\s*(?P<title>.+?)\.?$")


def _identifier_url(text: str) -> Optional[str]:
    m = _ARXIV_ID_RE.search(text)
    if m:
        return "https://arxiv.org/abs/" + m.group(1)
    m = _DOI_ID_RE.search(text)
    if m:
        doi = m.group(1).rstrip(".,;:")
        while doi.endswith(")") and doi.count(")") > doi.count("("):
            doi = doi[:-1].rstrip(".,;:")  # closing paren of the surrounding prose
        return "https://doi.org/" + doi
    return None


def _parse_bullet_citation(line: str, ordinal: int) -> Optional[ParsedCitation]:
    m = _BULLET_RE.match(line)
    if not m:
        return None
    body = m.group(1)
    url_match = _URL_RE.search(body)
    url = url_match.group(0) if url_match else _identifier_url(body)
    if url is None:
        return None  # a bullet with nothing resolvable is prose, not a citation
    rest = _LEADING_ID_RE.sub("", body).strip()
    apa = _APA_RE.match(_strip_md(rest)) if rest else None
    if apa:
        return ParsedCitation(
            ordinal=ordinal,
            source_type=_infer_source_type(url),
            title=apa.group("title").strip() or None,
            url=url,
            authors=apa.group("authors").strip() or None,
            year=apa.group("year"),
        )
    cit = _parse_citation_line(f"{ordinal}. {rest or body}")
    if cit is None:
        return None
    if cit.url is None:
        cit.url = url
        cit.source_type = _infer_source_type(url)
    return cit


def parse_citations(artifact_text: str, corpus_manifest: Optional[Any]) -> List[ParsedCitation]:
    """§5.3.1 priority: artifact references section, supplemented by corpus_manifest."""
    refs = _find_references_section(artifact_text)
    artifact_cits: Dict[int, ParsedCitation] = {}
    order: List[int] = []
    if refs:
        for raw in refs.splitlines():
            cit = _parse_citation_line(raw)
            if cit is None:
                cit = _parse_bullet_citation(raw, max(artifact_cits, default=0) + 1)
            if cit and cit.ordinal not in artifact_cits:
                artifact_cits[cit.ordinal] = cit
                order.append(cit.ordinal)

    corpus_cits: List[ParsedCitation] = []
    if isinstance(corpus_manifest, dict) and isinstance(corpus_manifest.get("citations"), list):
        for entry in corpus_manifest["citations"]:
            if not isinstance(entry, dict):
                continue
            ordinal = entry.get("ordinal")
            if not isinstance(ordinal, int):
                continue
            cit = ParsedCitation(
                ordinal=ordinal,
                source_type=entry.get("source_type") or _infer_source_type(entry.get("url")),
                title=entry.get("title"),
                url=entry.get("url"),
                authors=entry.get("authors"),
                year=entry.get("year"),
                excerpt=entry.get("excerpt"),
            )
            corpus_cits.append(cit)

    # supplement: fill nulls in artifact parse, append unmatched corpus entries
    for cit in corpus_cits:
        if cit.ordinal in artifact_cits:
            art = artifact_cits[cit.ordinal]
            if art.source_type is None:
                art.source_type = cit.source_type
            if art.title is None:
                art.title = cit.title
            if art.url is None:
                art.url = cit.url
            if art.authors is None:
                art.authors = cit.authors
            if art.year is None:
                art.year = cit.year
            if art.excerpt is None:
                art.excerpt = cit.excerpt
        else:
            artifact_cits[cit.ordinal] = cit
            order.append(cit.ordinal)

    if not artifact_cits and not corpus_cits:
        return []
    if not order:
        order = sorted(artifact_cits.keys())
    return [artifact_cits[o] for o in sorted(artifact_cits.keys())]


# --------------------------------------------------------------------------- #
# run_type + timestamps + content_hash + extensions
# --------------------------------------------------------------------------- #

def parse_run_type(run_id: str, config: Config) -> str:
    m = config.run_type_pattern.match(run_id)
    if m and m.groups():
        return m.group(1)
    return "unknown"


def _iso_now() -> str:
    import datetime as _dt

    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def compute_content_hash(
    question: str,
    findings: str,
    citations: List[ParsedCitation],
    method_notes: Optional[str],
    score: float,
) -> str:
    payload = {
        "question": question,
        "findings": findings,
        "citations": [c.canonical() for c in sorted(citations, key=lambda x: x.ordinal)],
        "method_notes": method_notes,
        "score": score,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_extensions(
    corpus_manifest: Optional[Any],
    claim_lens: Optional[Any],
    forced: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """FR-040: nested JSON with namespace keys. Validates top-level namespaces."""
    ext: Dict[str, Any] = {"sneferu": {"corpus_manifest": corpus_manifest, "claim_lens": claim_lens}}
    if forced is not None:
        ext["agentnews"] = {"forced_import": forced}
    # validate namespace keys
    for key in ext:
        if not re.match(r"^[a-z0-9]+$", key):
            raise IngestError("UNNAMESPACED_EXTENSION", key)
    return ext


# --------------------------------------------------------------------------- #
# Top-level parse
# --------------------------------------------------------------------------- #

def parse_run(run_dir: Path, config: Config) -> ParsedRun:
    """FR-001/FR-004: read + parse a Sneferu run directory read-only."""
    if not run_dir.exists() or not run_dir.is_dir():
        raise IngestError("BAD_INPUT", f"path not a directory: {run_dir}")

    run_id = run_dir.name

    sig_path = _find_signature(run_dir)
    signature = _read_json(sig_path, "signature.json")
    if not isinstance(signature, dict):
        raise IngestError("SCHEMA_VALIDATION", "signature.json:not an object")
    cost_data = None
    cost_path = run_dir / "cost.json"
    if cost_path.exists():
        cost_data = _read_json(cost_path, "cost.json")
    signature = normalize_signature(signature, cost_data)
    for key in SIG_REQUIRED_KEYS:
        if key not in signature:
            raise IngestError("SCHEMA_VALIDATION", f"signature.json:missing key {key}")
    if not isinstance(signature["model_families"], list):
        raise IngestError("SCHEMA_VALIDATION", "signature.json:model_families not array")

    artifact_path = run_dir / "builder_packet" / "artifact.md"
    artifact_text = _read_file(artifact_path, "builder_packet/artifact.md")
    if not artifact_text.strip():
        raise RejectError("EMPTY_ARTIFACT")

    problem_path = run_dir / "problem_statement.md"
    problem_text = _read_file(problem_path, "problem_statement.md")

    confidence_map = None
    cm_path = run_dir / "confidence_map.json"
    if cm_path.exists():
        confidence_map = _read_json(cm_path, "confidence_map.json")

    corpus_manifest = None
    cmf_path = run_dir / "corpus_manifest.json"
    if cmf_path.exists():
        corpus_manifest = _read_json(cmf_path, "corpus_manifest.json")

    claim_lens = None
    cl_path = run_dir / "claim_lens.json"
    if cl_path.exists():
        claim_lens = _read_json(cl_path, "claim_lens.json")

    artifact_meta, artifact_body = _strip_frontmatter(artifact_text)
    question = extract_question(problem_text, artifact_meta)
    findings_heading, findings_body = _find_findings_section(artifact_body)
    if findings_heading is None:
        # entire body minus methodology
        method = _find_methodology_section(artifact_body)
        findings_body = artifact_body
        if method is not None:
            findings_body = artifact_body.replace(method, "").strip()
    method_notes = _find_methodology_section(artifact_body)
    findings = findings_body.strip()
    summary = extract_summary(findings, artifact_meta)
    citations = parse_citations(artifact_body, corpus_manifest)
    quality_score = compute_quality_score(confidence_map, signature)
    run_type = parse_run_type(run_id, config)
    if run_type == "unknown":
        warnings.warn(f"run_type unknown for run_id={run_id}")
    run_started_at = signature.get("started_at") if isinstance(signature.get("started_at"), str) else None
    run_completed_at = signature.get("converged_at") if isinstance(signature.get("converged_at"), str) else None
    if run_completed_at is None:
        # parse leading timestamp from run_id
        m = re.match(r"^(\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2})Z", run_id)
        if m:
            run_completed_at = m.group(1) + "Z"
    extensions = build_extensions(corpus_manifest, claim_lens)
    content_hash = compute_content_hash(question, findings, citations, method_notes, quality_score)

    return ParsedRun(
        run_id=run_id,
        run_type=run_type,
        question=question,
        summary=summary,
        findings=findings,
        method_notes=method_notes,
        citations=citations,
        signature=signature,
        quality_score=quality_score,
        cost_data=cost_data,
        extensions=extensions,
        run_started_at=run_started_at,
        run_completed_at=run_completed_at,
        content_hash=content_hash,
    )


# --------------------------------------------------------------------------- #
# validate-sneferu-layout (FR-042)
# --------------------------------------------------------------------------- #

def validate_sneferu_layout(run_dir: Path, config: Config) -> Dict[str, Any]:
    """FR-042: compatibility report. Raises IngestError on required-file failure."""
    if not run_dir.exists() or not run_dir.is_dir():
        raise IngestError("BAD_INPUT", f"path not a directory: {run_dir}")
    report: Dict[str, Any] = {"required_files": {}, "optional_files": {}, "signature_schema": {}, "run_type": "unknown"}
    for name in REQUIRED_FILES:
        p = run_dir / name
        present = p.exists() and os.access(p, os.R_OK)
        report["required_files"][name] = "present" if present else "missing"
        if not present:
            raise IngestError("LAYOUT_INCOMPATIBLE", f"missing required file {name}")
    # signature schema
    sig_path = run_dir / "signature.json"
    try:
        sig = json.loads(sig_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise IngestError("LAYOUT_INCOMPATIBLE", f"signature.json:{exc.msg}")
    if not isinstance(sig, dict):
        raise IngestError("LAYOUT_INCOMPATIBLE", "signature.json:not an object")
    sig_report: Dict[str, Any] = {}
    cost_data = None
    try:
        cost_data = json.loads((run_dir / "cost.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        pass  # optional file; parse_run reports a malformed one loudly
    projected = normalize_signature(sig, cost_data)
    for key in SIG_REQUIRED_KEYS:
        if key in sig:
            sig_report[key] = "present"
        elif key in projected:
            sig_report[key] = "derived"
        else:
            sig_report[key] = "missing"
        if key not in projected:
            raise IngestError("LAYOUT_INCOMPATIBLE", f"signature.json:missing key {key}")
    report["signature_schema"] = sig_report
    for name in OPTIONAL_FILES:
        p = run_dir / name
        report["optional_files"][name] = "present" if p.exists() else "absent"
    report["run_type"] = parse_run_type(run_dir.name, config)
    return report
