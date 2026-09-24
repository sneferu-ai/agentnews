"""Real Sneferu run layout: signature projection + bullet reference lists.

The first fixtures were written in a flat signature shape
(``degraded`` / ``model_families`` at top level) and ordinal-numbered
references. Real Sneferu research runs use neither, so every real run was
refused at import. ``run-sneferu-real`` is a trimmed copy of a real run's
signature (cast, degradation, signature_class, convergence) with a planted
``run_config`` that must never reach subscribers.
"""
import json

from agentnews import filter as agentnews_filter
from agentnews import ingest
from agentnews.config import load_config

from conftest import FIXTURES

REAL = FIXTURES / "run-sneferu-real"


def test_real_signature_projects_onto_flat_keys():
    parsed = ingest.parse_run(REAL, load_config())
    sig = parsed.signature
    assert sig["degraded"] is False
    assert sig["model_families"] == sorted(set(sig["model_families"]))
    # cost.json says which models actually ran; the configured-but-idle
    # spec_maxxer seat (gpt-5.4) is not an independent trainer of this bundle.
    assert sig["model_families"] == ["deepseek", "minimax", "moonshot", "zhipu"]
    assert sig["model_families_from"] == "dispatched_models"
    assert sig["cross_vendor"] is True  # signature_class.distinct_trainers == 4
    assert sig["signature_class"] == "clean_cross_vendor"
    assert sig["adversary_verdict_class"] == "remaining_objection"
    assert sig["converged_at"] == parsed.run_completed_at
    assert 0 < sig["convergence_score"] <= 100
    assert sig["run_grade"] == {"controller_integrity": "green", "artifact_quality": "yellow"}


def test_without_cost_json_families_come_from_the_configured_cast():
    raw = json.loads((REAL / "signature.json").read_text())
    sig = ingest.normalize_signature(raw)
    assert sig["model_families_from"] == "configured_cast"
    assert "openai" in sig["model_families"]


def test_projection_drops_internal_run_config_and_params():
    parsed = ingest.parse_run(REAL, load_config())
    blob = json.dumps(parsed.signature)
    assert "run_config" not in parsed.signature and "cast" not in parsed.signature
    assert "/Users/operator" not in blob and "max_tokens" not in blob


def test_real_degradation_is_rejected(tmp_path):
    raw = json.loads((REAL / "signature.json").read_text())
    raw["degradation"]["degraded"] = True
    d = tmp_path / "degraded-run"
    (d / "builder_packet").mkdir(parents=True)
    (d / "signature.json").write_text(json.dumps(raw))
    (d / "problem_statement.md").write_text((REAL / "problem_statement.md").read_text())
    (d / "builder_packet" / "artifact.md").write_text((REAL / "builder_packet" / "artifact.md").read_text())
    (d / "confidence_map.json").write_text((REAL / "confidence_map.json").read_text())
    parsed = ingest.parse_run(d, load_config())
    admitted, rejects = agentnews_filter.admit(parsed, load_config(), skip_verify=True)
    assert not admitted and "DEGRADED" in rejects


def test_signature_class_degraded_also_counts(tmp_path):
    raw = json.loads((REAL / "signature.json").read_text())
    raw["signature_class"]["degraded"] = True
    assert ingest.normalize_signature(raw)["degraded"] is True


def test_unverified_model_identity_claims_no_cross_vendor():
    raw = json.loads((REAL / "signature.json").read_text())
    raw["signature_class"]["unverified"] = True
    assert ingest.normalize_signature(raw)["cross_vendor"] is None


def test_bullet_references_become_citations_with_resolvable_urls():
    parsed = ingest.parse_run(REAL, load_config())
    cits = parsed.citations
    assert [c.ordinal for c in cits] == [1, 2, 3, 4]  # the identifier-less bullet is prose
    assert cits[0].url == "https://arxiv.org/abs/2201.11903"
    assert cits[0].title == "Chain-of-Thought Prompting Elicits Reasoning in Large Language Models"
    assert cits[0].authors == "Wei et al." and cits[0].year == "2022"
    assert cits[0].source_type == "arxiv"
    assert cits[3].url == "https://doi.org/10.48550/arXiv.2305.20050"


def test_real_run_is_admitted():
    parsed = ingest.parse_run(REAL, load_config())
    admitted, rejects = agentnews_filter.admit(parsed, load_config(), skip_verify=True)
    assert admitted, rejects
    assert parsed.quality_score == 74.0  # confidence_map overall_confidence wins


def test_validate_layout_reports_derived_keys():
    report = ingest.validate_sneferu_layout(REAL, load_config())
    assert report["signature_schema"] == {"degraded": "derived", "model_families": "derived"}


def test_flat_signature_passes_through_unchanged():
    flat = {"degraded": False, "model_families": ["a", "b"], "cast": {"x": {}}}
    assert ingest.normalize_signature(flat) is flat


def test_unknown_shape_still_fails_loudly():
    assert ingest.normalize_signature({"degraded": False}) == {"degraded": False}


def test_ordinal_references_are_unaffected():
    line = "[1] **X** — https://doi.org/10.1/abc (A, 2024)"
    assert ingest._parse_citation_line(line).title == "X"
