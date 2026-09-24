"""Citation identity: a link that resolves to the WRONG paper is not verified.

Found on a real Sneferu research run (2026-06-13, chain-of-thought
faithfulness): 4 of its 15 arXiv IDs resolve to unrelated papers, and the
resolution-only check marked all 15 "Link resolved". The four real pairs are
pinned below.
"""
from agentnews import filter as filt
from agentnews.ingest import ParsedCitation
from agentnews.render import _html_env

REAL_MISMATCHES = [
    ("On the Failure of LLM Self-Correction",
     "NEFTune: Noisy Embeddings Improve Instruction Finetuning"),
    ("Scaling Monosemanticity: Extracting Interpretable Features from Claude 3 Sonnet",
     "LVDiffusor: Distilling Functional Rearrangement Priors from Large Models into Diffusor"),
    ("Training a Helpful and Harmless Assistant with Reinforcement Learning from Human Feedback",
     "Training language models to follow instructions with human feedback"),
    ("How to think of Chain-of-Thought as a causal model?",
     "Absolute light yield of the EJ-204 plastic scintillator"),
]

REAL_MATCHES = [
    ("Self-Refine", "Self-Refine: Iterative Refinement with Self-Feedback"),
    ("Language Models Don\u2019t Always Say What They Think",
     "Language Models Don&#39;t Always Say What They Think: Unfaithful Explanations in Chain-of-Thought Prompting"),
    ("Eliciting Latent Predictions with the Tuned Lens",
     "Eliciting Latent Predictions from Transformers with the Tuned Lens"),
    ("Progress Measures for Grokking via Mechanistic Interpretability",
     "Progress measures for grokking via mechanistic interpretability"),
    ("Constitutional AI", "Constitutional AI: Harmlessness from AI Feedback"),
]


def test_real_mismatches_are_caught():
    for claimed, actual in REAL_MISMATCHES:
        assert not filt.title_matches(claimed, actual), claimed


def test_shortened_and_reformatted_titles_still_match():
    for claimed, actual in REAL_MATCHES:
        assert filt.title_matches(claimed, actual), claimed


def _cit(url, title, verified=1):
    c = ParsedCitation(ordinal=1, source_type="arxiv", title=title, url=url)
    c.verified = verified
    return c


def test_check_identity_downgrades_wrong_paper(monkeypatch):
    monkeypatch.setattr(filt, "_fetch_page_title", lambda url, timeout=10: REAL_MISMATCHES[0][1])
    c = _cit("https://arxiv.org/abs/2310.05914", REAL_MISMATCHES[0][0])
    filt.check_identity(c)
    assert c.verified == filt.IDENTITY_MISMATCH
    assert not filt._counts_toward_minimum(c, skip=False)


def test_check_identity_keeps_right_paper(monkeypatch):
    monkeypatch.setattr(filt, "_fetch_page_title", lambda url, timeout=10: REAL_MATCHES[0][1])
    c = _cit("https://arxiv.org/abs/2303.17651", "Self-Refine")
    filt.check_identity(c)
    assert c.verified == 1


def test_unreadable_page_title_leaves_resolution_verdict(monkeypatch):
    monkeypatch.setattr(filt, "_fetch_page_title", lambda url, timeout=10: None)
    c = _cit("https://arxiv.org/abs/2303.17651", "Anything")
    filt.check_identity(c)
    assert c.verified == 1


def test_non_arxiv_and_unresolved_links_are_not_fetched(monkeypatch):
    def boom(url, timeout=10):
        raise AssertionError("must not fetch")
    monkeypatch.setattr(filt, "_fetch_page_title", boom)
    for c in (_cit("https://example.com/paper", "X"), _cit("https://arxiv.org/abs/1", "X", verified=-2)):
        filt.check_identity(c)


def test_title_extraction_reads_arxiv_markup():
    body = '<title>[2303.17651] Self-Refine: Iterative Refinement with Self-Feedback</title>'
    m = filt._HTML_TITLE_RE.search(body)
    assert m.group(1) == "Self-Refine: Iterative Refinement with Self-Feedback"


def test_badge_names_the_wrong_paper_state():
    env = _html_env()
    tmpl = env.from_string('{% from "_macros.html" import verification_badge %}{{ verification_badge(c) }}')
    out = tmpl.render(c={"verified": -3})
    assert "Wrong paper" in out and "Link resolved" not in out
