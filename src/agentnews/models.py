"""Pydantic response models (FR-009, FR-010, FR-022, FR-032, FR-033, §5.1).

Kept Python-3.9-compatible (Optional/List/Dict from typing) so the test
environment's interpreter can collect them.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


_MODELS_CONFIG = ConfigDict(populate_by_name=True)


class CitationOut(BaseModel):
    ordinal: int
    source_type: Optional[str] = None
    title: Optional[str] = None
    url: Optional[str] = None
    authors: Optional[str] = None
    year: Optional[str] = None
    verified: int = 0
    excerpt: Optional[str] = None
    accessed_at: Optional[str] = None


class SignatureSummary(BaseModel):
    model_families: List[str] = Field(default_factory=list)
    cross_vendor: Optional[bool] = None
    degraded: bool = False


class ReuseTerms(BaseModel):
    license: str = "agentnews-research-v1"
    attribution_required: bool = True
    may_republish: bool = True
    derivative_allowed: bool = True
    commercial_allowed: bool = True
    max_excerpt_words: int = 300
    terms_version: str = "1.0"
    terms_url: str = ""
    disclaimer: str = ""
    generated_at: str = ""


class PackageListEntry(BaseModel):
    id: str
    question: str
    score: float
    signature_summary: SignatureSummary
    published_at: Optional[str] = None
    article_url: str
    latency_disclosure: str = ""


class PackageList(BaseModel):
    model_config = _MODELS_CONFIG
    schema_: str = Field(default="agentnews.package_list/v1", alias="schema")
    packages: List[PackageListEntry] = Field(default_factory=list)
    count: int = 0
    limit: int = 20
    offset: int = 0


class PackageFull(BaseModel):
    model_config = _MODELS_CONFIG
    schema_: str = Field(default="agentnews.package/v1", alias="schema")
    id: str
    run_id: str
    run_type: Optional[str] = None
    question: str
    summary: Optional[str] = None
    findings: str
    citations: List[CitationOut] = Field(default_factory=list)
    method_notes: Optional[str] = None
    score: float
    signature: Dict[str, Any] = Field(default_factory=dict)
    reuse_terms: ReuseTerms
    content_hash: str
    article_url: str
    latency_disclosure: str = ""
    extensions: Dict[str, Any] = Field(default_factory=dict)
    created_at: Optional[str] = None
    run_started_at: Optional[str] = None
    run_completed_at: Optional[str] = None
    published_at: Optional[str] = None


class CatalogEntry(BaseModel):
    id: str
    question: str
    score: float
    signature_summary: SignatureSummary
    published_at: Optional[str] = None
    article_url: str


class Catalog(BaseModel):
    model_config = _MODELS_CONFIG
    schema_: str = Field(default="agentnews.catalog/v1", alias="schema")
    packages: List[CatalogEntry] = Field(default_factory=list)
    count: int = 0
    limit: int = 20
    offset: int = 0


class CitationPreview(BaseModel):
    ordinal: int
    title: Optional[str] = None
    source_type: Optional[str] = None


class SampleOut(BaseModel):
    id: str
    question: str
    score: float
    signature_summary: SignatureSummary
    summary: Optional[str] = None
    citation_previews: List[CitationPreview] = Field(default_factory=list)
    article_url: str
    access_url: str


class VerifyOut(BaseModel):
    package_id: str
    content_hash_stored: str
    content_hash_recomputed: str
    match: bool
    signature: Dict[str, Any] = Field(default_factory=dict)
    verified_at: str
    verification_scope: str = "post_import_tamper_detection"


class ErrorBody(BaseModel):
    code: str
    message: str


class ErrorOut(BaseModel):
    error: ErrorBody


class HealthOut(BaseModel):
    status: str
    packages_count: int = 0
    db_ok: bool = True
