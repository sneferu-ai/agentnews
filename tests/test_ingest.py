"""Tests for the Sneferu run parser (FR-001, FR-003, FR-004, FR-040, FR-042)."""
import json
from pathlib import Path

import pytest

from agentnews import ingest
from agentnews.config import load_config


def test_parse_run_good(good_run_dir):
    config = load_config()
    parsed = ingest.parse_run(good_run_dir, config)
    assert parsed.run_id == "run-good"
    assert parsed.run_type == "unknown"  # fixture dir name doesn't match regex
    assert "sub-agent" in parsed.question.lower()
    assert "Parallel" in parsed.findings or "parallel" in parsed.findings.lower()
    assert parsed.quality_score == pytest.approx(78.5)
    assert parsed.signature["degraded"] is False
    assert parsed.signature["model_families"] == ["openai", "anthropic", "fireworks"]
    assert len(parsed.citations) == 5  # 4 from artifact + 1 supplemented from corpus
    ordinals = [c.ordinal for c in parsed.citations]
    assert ordinals == [1, 2, 3, 4, 5]
    c1 = next(c for c in parsed.citations if c.ordinal == 1)
    assert c1.title == "Parallel multi-agent evidence gathering"
    assert c1.url == "https://example.com/paper-1"
    assert c1.authors == "Smith, Lee"
    assert c1.year == "2024"
    assert c1.source_type == "web"
    # corpus excerpt supplemented the artifact citation
    assert c1.excerpt and "parallel agents" in c1.excerpt.lower()
    c5 = next(c for c in parsed.citations if c.ordinal == 5)
    assert c5.title == "Synthesis model attention budgets"
    assert parsed.content_hash.startswith("sha256:")
    assert len(parsed.content_hash) == len("sha256:") + 64


def test_parse_run_question_extraction(good_run_dir):
    config = load_config()
    parsed = ingest.parse_run(good_run_dir, config)
    assert parsed.question.startswith("Should agent teams")
    assert parsed.summary  # from artifact frontmatter summary
    assert "parallel sub-agent sweeps" in parsed.summary.lower()


def test_parse_run_methodology(good_run_dir):
    config = load_config()
    parsed = ingest.parse_run(good_run_dir, config)
    assert parsed.method_notes is not None
    assert "40 trials" in parsed.method_notes


def test_quality_score_score_key():
    cm = {"score": 82.0}
    assert ingest.compute_quality_score(cm, {}) == pytest.approx(82.0)


def test_quality_score_overall_confidence_normalized():
    cm = {"overall_confidence": 0.75}
    assert ingest.compute_quality_score(cm, {}) == pytest.approx(75.0)


def test_quality_score_leaf_mean_normalized():
    cm = {"axis_scores": {"breadth": 0.8, "depth": 0.6}}
    assert ingest.compute_quality_score(cm, {}) == pytest.approx(70.0)


def test_quality_score_leaf_mean_already_0_100():
    cm = {"rubric": {"coverage": 80, "rigor": 70}}
    assert ingest.compute_quality_score(cm, {}) == pytest.approx(75.0)


def test_quality_score_fallback_convergence():
    cm = {}
    assert ingest.compute_quality_score(cm, {"convergence_score": 65.0}) == pytest.approx(65.0)


def test_quality_score_zero_when_nothing():
    assert ingest.compute_quality_score(None, {}) == 0.0


def test_quality_score_ignores_bools():
    cm = {"ok": True, "done": False, "score": 50.0}
    # bools must not contribute; score key wins
    assert ingest.compute_quality_score(cm, {}) == pytest.approx(50.0)


def test_quality_score_recursive_leaf_mean():
    cm = {"a": {"b": {"c": 0.9, "d": 0.5}}}
    assert ingest.compute_quality_score(cm, {}) == pytest.approx(70.0)


def test_citation_line_formats():
    lines = [
        "[1] **Title One** — https://example.com/a (Smith, 2024)",
        "[2] Title Two. Jones. 2023. URL: https://example.com/b",
        "3. [Title Three](https://example.com/c) (Patel, and Kim, 2025)",
        "4. Title Four — https://example.com/d (Garcia, 2022)",
    ]
    cits = [ingest._parse_citation_line(l) for l in lines]
    assert all(c is not None for c in cits)
    assert cits[0].ordinal == 1 and cits[0].title == "Title One"
    assert cits[1].ordinal == 2 and cits[1].title == "Title Two"
    assert cits[2].ordinal == 3 and cits[2].title == "Title Three"
    assert cits[3].ordinal == 4 and cits[3].title == "Title Four"
    assert all(c.url and c.url.startswith("https://") for c in cits)


def test_citation_doi_arxiv_source_type():
    c = ingest._parse_citation_line("[1] **X** — https://doi.org/10.1/abc (A, 2024)")
    assert c.source_type == "doi"
    c = ingest._parse_citation_line("[2] **Y** — https://arxiv.org/abs/2401.1 (B, 2024)")
    assert c.source_type == "arxiv"


def test_citation_no_url_is_other():
    c = ingest._parse_citation_line("[1] **Z** (C, 2024)")
    assert c.url is None
    assert c.source_type == "other"


def test_content_hash_stable(good_run_dir):
    config = load_config()
    a = ingest.parse_run(good_run_dir, config)
    b = ingest.parse_run(good_run_dir, config)
    assert a.content_hash == b.content_hash


def test_content_hash_changes_with_findings(good_run_dir, tmp_path):
    import shutil
    run2 = tmp_path / "run-good2"
    shutil.copytree(good_run_dir, run2)
    art = run2 / "builder_packet" / "artifact.md"
    art.write_text(art.read_text().replace("34%", "35%"))
    config = load_config()
    a = ingest.parse_run(good_run_dir, config)
    b = ingest.parse_run(run2, config)
    assert a.content_hash != b.content_hash


def test_extensions_namespaced(good_run_dir):
    config = load_config()
    parsed = ingest.parse_run(good_run_dir, config)
    assert "sneferu" in parsed.extensions
    assert "agentnews" not in parsed.extensions  # no forced import here
    assert isinstance(parsed.extensions["sneferu"]["corpus_manifest"], dict)


def test_missing_required_file_raises(tmp_path):
    d = tmp_path / "empty"
    d.mkdir()
    config = load_config()
    with pytest.raises(ingest.IngestError) as ei:
        ingest.parse_run(d, config)
    assert "MISSING_FILE" in str(ei.value) or "LAYOUT" in str(ei.value)


def test_missing_signature_raises(tmp_path):
    d = tmp_path / "nosig"
    d.mkdir()
    (d / "problem_statement.md").write_text("Q?")
    (d / "builder_packet").mkdir()
    (d / "builder_packet" / "artifact.md").write_text("# X\n\n## Findings\nbody")
    config = load_config()
    with pytest.raises(ingest.IngestError) as ei:
        ingest.parse_run(d, config)
    assert "signature" in str(ei.value)


def test_signature_missing_key_raises(tmp_path):
    d = tmp_path / "badkey"
    d.mkdir()
    (d / "signature.json").write_text(json.dumps({"degraded": False}))
    (d / "problem_statement.md").write_text("Q?")
    (d / "builder_packet").mkdir()
    (d / "builder_packet" / "artifact.md").write_text("# X\n\n## Findings\nbody")
    config = load_config()
    with pytest.raises(ingest.IngestError) as ei:
        ingest.parse_run(d, config)
    assert "SCHEMA_VALIDATION" in str(ei.value)


def test_empty_artifact_rejects(tmp_path):
    d = tmp_path / "emptyart"
    d.mkdir()
    (d / "signature.json").write_text(json.dumps({"degraded": False, "model_families": ["x"]}))
    (d / "problem_statement.md").write_text("Q?")
    (d / "builder_packet").mkdir()
    (d / "builder_packet" / "artifact.md").write_text("   \n  \n")
    config = load_config()
    with pytest.raises(ingest.RejectError):
        ingest.parse_run(d, config)


def test_validate_sneferu_layout_good(good_run_dir):
    config = load_config()
    report = ingest.validate_sneferu_layout(good_run_dir, config)
    assert all(v == "present" for v in report["required_files"].values())
    assert report["signature_schema"]["degraded"] == "present"
    assert report["signature_schema"]["model_families"] == "present"


def test_validate_sneferu_layout_missing(tmp_path):
    d = tmp_path / "incomplete"
    d.mkdir()
    config = load_config()
    with pytest.raises(ingest.IngestError):
        ingest.validate_sneferu_layout(d, config)


def test_parse_run_read_only(good_run_dir):
    """FR-023: parsing must not mutate the source directory."""
    import hashlib
    before = {
        p: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in good_run_dir.rglob("*") if p.is_file()
    }
    config = load_config()
    ingest.parse_run(good_run_dir, config)
    after = {
        p: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in good_run_dir.rglob("*") if p.is_file()
    }
    assert before == after


def test_run_type_regex():
    from agentnews.config import load_config
    config = load_config()
    assert ingest.parse_run_type("2026-08-15T10-00-00Z-research-abc123", config) == "research"
    assert ingest.parse_run_type("2026-08-15T10-00-00Z-spec-deadbeef", config) == "spec"
    assert ingest.parse_run_type("run-good", config) == "unknown"


def test_parse_run_warns_unknown_run_type(good_run_dir, config):
    """FR-004: parsing a run_id that does not match the run-type regex emits a warning."""
    import warnings
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        ingest.parse_run(good_run_dir, config)
    assert any("run_type unknown" in str(warning.message) for warning in w)
