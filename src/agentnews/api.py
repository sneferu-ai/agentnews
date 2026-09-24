"""AgentNews FastAPI service (FR-009, FR-010, FR-013, FR-036, FR-038).

Lifespan owns: config load + env validation, DB connect + migrations, rate
limiter warm, PID file. Middleware owns: security headers, request timeout,
request logging (FR-036), ETag (FR-038). Auth via API key (X-API-Key header) for
subscriber endpoints; admin operations are CLI-only (FR-027).
"""
from __future__ import annotations

import asyncio
import hashlib
import time
import uuid
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.types import ASGIApp

from . import auth, db, ingest, ratelimit, render, storage
from .config import (
    Config,
    EnvValidationError,
    ensure_log_salt,
    load_config,
    validate_required_env,
)
from .models import (
    Catalog,
    CatalogEntry,
    CitationOut,
    CitationPreview,
    ErrorBody,
    ErrorOut,
    HealthOut,
    PackageFull,
    PackageList,
    PackageListEntry,
    ReuseTerms,
    SampleOut,
    SignatureSummary,
    VerifyOut,
)

REQUEST_TIMEOUT_S = 30.0
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
    "Content-Security-Policy": "default-src 'self'; script-src 'none'; style-src 'self'; img-src 'self' data:;",

}


# --------------------------------------------------------------------------- #
# App state
# --------------------------------------------------------------------------- #

class AppState:
    config: Config
    conn: object  # sqlite3.Connection
    keys: auth.KeyCache
    limiter: ratelimit.RateLimiter


_state = AppState()


def get_state() -> AppState:
    return _state


def get_conn() -> object:
    return _state.conn


def get_config() -> Config:
    return _state.config


# --------------------------------------------------------------------------- #
# Hash helpers for logging (§5.2)
# --------------------------------------------------------------------------- #

def _hash_with_salt(value: str, salt: str) -> str:
    if not salt:
        salt = "agentnews-default-salt"
    return hashlib.sha256((value + "|" + salt).encode("utf-8")).hexdigest()[:16]


def _truncate_ipv6(ip: str) -> str:
    """FR-022: truncate IPv6 addresses to their /64 prefix."""
    try:
        import ipaddress
        iface = ipaddress.ip_interface(f"{ip}/64")
        if iface.version == 6:
            return str(iface.network.network_address)
    except (ValueError, TypeError):
        pass
    return ip


def _client_ip(request: Request, config: Config) -> str:
    if config.trust_proxy_headers:
        xff = request.headers.get("x-forwarded-for")
        if xff:
            # FR-022: last entry is added by the reverse proxy (trusted).
            return xff.split(",")[-1].strip()
    if request.client is not None:
        return _truncate_ipv6(request.client.host)
    return "unknown"


# --------------------------------------------------------------------------- #
# Lifespan
# --------------------------------------------------------------------------- #

from contextlib import asynccontextmanager  # noqa: E402


@asynccontextmanager
async def lifespan(app: FastAPI):
    config = load_config()
    try:
        validate_required_env(config)
    except EnvValidationError as exc:
        # In test contexts the env may be set on the process; still surface.
        raise RuntimeError("; ".join(exc.errors)) from exc
    if not config.log_salt:
        ensure_log_salt()
        config = load_config()
    conn = db.connect(config.db_path)
    await db.run_in_threadpool(db.run_migrations, conn)
    keys = auth.KeyCache()
    limiter = ratelimit.RateLimiter(
        per_day=config.rate_limit_per_day, burst=config.burst_allowance
    )
    await db.run_in_threadpool(limiter.warm_from_log, conn)
    await db.run_in_threadpool(db.write_pid_file, config.db_path)
    _state.config = config
    _state.conn = conn
    _state.keys = keys
    _state.limiter = limiter
    try:
        yield
    finally:
        await db.run_in_threadpool(db.remove_pid_file, config.db_path)
        try:
            conn.close()
        except Exception:
            pass


def create_app(config: Optional[Config] = None, conn: Optional[object] = None,
               run_lifespan: bool = True) -> FastAPI:
    """Build the FastAPI app. Tests pass a pre-built config + conn and skip lifespan."""
    app = FastAPI(
        title="AgentNews",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan if run_lifespan else None,
    )
    app.mount("/static", StaticFiles(directory=str(render.STATIC_DIR)), name="static")

    if config is not None:
        _state.config = config
    if conn is not None:
        _state.conn = conn
    if not run_lifespan:
        _state.keys = auth.KeyCache()
        _state.limiter = ratelimit.RateLimiter(
            per_day=(config.rate_limit_per_day if config else 100),
            burst=(config.burst_allowance if config else 20),
        )

    _register_middleware(app)
    _register_exception_handlers(app)
    _register_routes(app)
    return app


# `app` is constructed at the bottom of the module (after the route/middleware
# registrars are defined).


# --------------------------------------------------------------------------- #
# Middleware
# --------------------------------------------------------------------------- #

def _register_middleware(app: FastAPI) -> None:
    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        for key, val in SECURITY_HEADERS.items():
            response.headers.setdefault(key, val)
        return response

    @app.middleware("http")
    async def rate_limit_headers(request: Request, call_next):
        """FR-013: surface remaining quota on gated responses."""
        response = await call_next(request)
        if hasattr(request.state, "rate_limit_remaining"):
            response.headers["X-RateLimit-Remaining"] = str(request.state.rate_limit_remaining)
        if hasattr(request.state, "rate_limit_ip_remaining"):
            response.headers["X-RateLimit-Ip-Remaining"] = str(request.state.rate_limit_ip_remaining)
        # FR-014: X-RateLimit-Warning: burst header when serving from burst zone.
        if hasattr(request.state, "rate_limit_warning"):
            response.headers["X-RateLimit-Warning"] = request.state.rate_limit_warning
        return response

    @app.middleware("http")
    async def request_timeout(request: Request, call_next):
        try:
            return await asyncio.wait_for(call_next(request), timeout=REQUEST_TIMEOUT_S)
        except asyncio.TimeoutError:
            return JSONResponse(
                {"error": {"code": "timeout", "message": "request exceeded time limit"}},
                status_code=504,
            )

    @app.middleware("http")
    async def request_logger(request: Request, call_next):
        start = time.monotonic()
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
        request.state.request_id = request_id
        response = await call_next(request)
        elapsed_ms = int((time.monotonic() - start) * 1000)
        try:
            config = _state.config
            conn = _state.conn
            if config is not None and conn is not None:
                ip_hash = _hash_with_salt(_client_ip(request, config), config.log_salt)
                ua_hash = _hash_with_salt(request.headers.get("user-agent", ""), config.log_salt)
                kp = getattr(request.state, "key_prefix", None)
                await db.run_in_threadpool(
                    storage.log_request,
                    conn, kp, request.method, request.url.path,
                    response.status_code, elapsed_ms, ip_hash, ua_hash, request_id,
                )
        except Exception:
            pass
        response.headers["X-Request-Id"] = request_id
        response.headers["X-Response-Time-Ms"] = str(elapsed_ms)
        return response

    @app.middleware("http")
    async def etag_middleware(request: Request, call_next):
        response = await call_next(request)
        if request.method != "GET":
            return response
        if response.status_code != 200:
            return response
        body = b""
        async for chunk in response.body_iterator:
            body += chunk
        etag = '"' + hashlib.sha256(body).hexdigest()[:16] + '"'
        response.headers["ETag"] = etag
        inm = request.headers.get("if-none-match")
        if inm and inm == etag:
            # RFC 9110: a 304 has no body. Copying the 200's Content-Length
            # onto the bodyless response makes uvicorn raise "Response content
            # shorter than Content-Length" AFTER headers are sent, killing the
            # keep-alive connection — the next pooled request gets ECONNRESET.
            headers_304 = dict(response.headers)
            headers_304.pop("content-length", None)
            return Response(status_code=304, headers=headers_304)
        new_resp = Response(
            content=body,
            status_code=response.status_code,
            headers=dict(response.headers),
            media_type=response.media_type,
        )
        return new_resp


# --------------------------------------------------------------------------- #
# Exception handlers — unwrap `detail` so error bodies are flat JSON
# --------------------------------------------------------------------------- #

def _register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(HTTPException)
    async def http_exc_handler(request: Request, exc: HTTPException):
        detail = exc.detail
        headers = {}
        # FR-014: Retry-After header on 429 responses.
        if exc.status_code == 429 and hasattr(request.state, "retry_after"):
            headers["Retry-After"] = str(request.state.retry_after)
        # HTML 404 for browser-facing routes; JSON for API/machine routes.
        path = request.url.path
        is_api_path = path.startswith("/v1/") or path in ("/health", "/healthz", "/feed.xml", "/v1/openapi.json")
        if exc.status_code == 404 and not is_api_path:
            return HTMLResponse(
                render.render_404(_state.config),
                status_code=404,
                headers=headers,
            )
        if isinstance(detail, dict) and "error" in detail:
            return JSONResponse(status_code=exc.status_code, content=detail, headers=headers)
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": str(exc.status_code), "message": str(detail)}},
            headers=headers,
        )


# --------------------------------------------------------------------------- #
# Auth dependencies
# --------------------------------------------------------------------------- #

def _extract_bearer(authorization: Optional[str]) -> Optional[str]:
    if not authorization:
        return None
    parts = authorization.split(None, 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1].strip()
    return None


def _extract_api_key(request: Request) -> Optional[str]:
    """Prefer X-API-Key header; fall back to Authorization: Bearer."""
    x_api_key = request.headers.get("x-api-key")
    if x_api_key:
        return x_api_key.strip()
    authorization = request.headers.get("authorization")
    return _extract_bearer(authorization)


async def require_api_key(request: Request) -> str:
    """FR-012: validate API key + enforce rate limit. Returns key_prefix."""
    key = _extract_api_key(request)
    if not key:
        raise HTTPException(
            status_code=401,
            detail={"error": {"code": "unauthorized", "message": "missing X-API-Key"}},
        )
    conn = _state.conn
    valid, reason = await db.run_in_threadpool(auth.validate_key, key, conn, _state.keys)
    if not valid:
        raise HTTPException(status_code=401, detail={"error": {"code": reason or "unauthorized", "message": "invalid or revoked key"}})
    kp = key[:8]
    request.state.key_prefix = kp
    if not _state.limiter.allow_key(kp):
        # FR-014: Retry-After header on 429.
        request.state.retry_after = _state.limiter.retry_after_key(kp)
        raise HTTPException(status_code=429, detail={"error": {"code": "rate_limited", "message": "daily limit exceeded"}})
    request.state.rate_limit_remaining = _state.limiter.remaining_key(kp)
    # FR-014: X-RateLimit-Warning: burst when serving from burst zone.
    if _state.limiter.is_burst_key(kp):
        request.state.rate_limit_warning = "burst"
    return kp


async def ip_limit(request: Request) -> None:
    """FR-022: per-IP cap for unauthenticated /v1/sample (10/day)."""
    config = _state.config
    ip_hash = _hash_with_salt(_client_ip(request, config), config.log_salt)
    if not _state.limiter.allow_ip(ip_hash):
        request.state.retry_after = _state.limiter.retry_after_ip(ip_hash)
        raise HTTPException(status_code=429, detail={"error": {"code": "rate_limited", "message": "ip daily limit exceeded"}})
    request.state.rate_limit_ip_remaining = _state.limiter.remaining_ip(ip_hash)


async def ip_limit_catalog(request: Request) -> None:
    """FR-032: per-IP cap for unauthenticated /v1/catalog (60/day)."""
    config = _state.config
    ip_hash = _hash_with_salt(_client_ip(request, config), config.log_salt)
    if not _state.limiter.allow_ip_catalog(ip_hash):
        request.state.retry_after = _state.limiter.retry_after_ip_catalog(ip_hash)
        raise HTTPException(status_code=429, detail={"error": {"code": "rate_limited", "message": "ip daily limit exceeded"}})
    request.state.rate_limit_ip_remaining = _state.limiter.remaining_ip_catalog(ip_hash)


async def ua_limit(request: Request) -> None:
    """FR-022: secondary per-user_agent_hash cap for /v1/sample (10/day)."""
    config = _state.config
    ua_hash = _hash_with_salt(request.headers.get("user-agent", ""), config.log_salt)
    if not _state.limiter.allow_ua(ua_hash):
        request.state.retry_after = _state.limiter.retry_after_ua(ua_hash)
        raise HTTPException(status_code=429, detail={"error": {"code": "rate_limited", "message": "user agent daily limit exceeded"}})


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _sig_summary(sig: dict) -> SignatureSummary:
    return SignatureSummary(
        model_families=list(sig.get("model_families") or []),
        cross_vendor=sig.get("cross_vendor"),
        degraded=bool(sig.get("degraded", False)),
    )


def _citations_out(cits: list) -> list:
    return [
        CitationOut(
            ordinal=c["ordinal"], source_type=c["source_type"], title=c["title"],
            url=c["url"], authors=c["authors"], year=c["year"], verified=c["verified"],
            excerpt=c["excerpt"], accessed_at=c["accessed_at"],
        )
        for c in cits
    ]


def _reuse_terms(pkg: dict) -> ReuseTerms:
    rt = pkg.get("reuse_terms") or {}
    return ReuseTerms(**rt) if isinstance(rt, dict) else ReuseTerms()


def _package_full(pkg: dict, cits: list, config: Config) -> PackageFull:
    return PackageFull(
        id=pkg["id"],
        run_id=pkg["run_id"],
        run_type=pkg.get("run_type"),
        question=pkg["question"],
        summary=pkg.get("summary"),
        findings=pkg["findings"],
        citations=_citations_out(cits),
        method_notes=pkg.get("method_notes"),
        score=pkg["quality_score"],
        signature=pkg.get("signature") or {},
        reuse_terms=_reuse_terms(pkg),
        content_hash=pkg["content_hash"],
        article_url=f"{config.public_url}/articles/{pkg['slug']}",
        latency_disclosure=pkg.get("latency_disclosure") or "",
        extensions=pkg.get("extensions") or {},
        created_at=pkg.get("created_at"),
        run_started_at=pkg.get("run_started_at"),
        run_completed_at=pkg.get("run_completed_at"),
        published_at=pkg.get("published_at"),
    )


def _entry(pkg: dict, config: Config) -> PackageListEntry:
    return PackageListEntry(
        id=pkg["id"],
        question=pkg["question"],
        score=pkg["quality_score"],
        signature_summary=_sig_summary(pkg.get("signature") or {}),
        published_at=pkg.get("published_at"),
        article_url=f"{config.public_url}/articles/{pkg['slug']}",
        latency_disclosure=pkg.get("latency_disclosure") or "",
    )


async def _transition_staged() -> None:
    """FR-006: lazily transition staged packages whose publish_after has passed.

    Called on each request to the five trigger endpoints: /v1/packages,
    /v1/packages/{id}, /v1/catalog, /v1/sample, and /feed.xml.
    """
    conn = _state.conn
    config = _state.config
    if conn is not None and config is not None:
        try:
            await db.run_in_threadpool(storage.transition_staged_to_published, conn, config)
        except Exception:
            pass


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #

def _register_routes(app: FastAPI) -> None:

    @app.get("/health", response_model=HealthOut, tags=["health"])
    @app.get("/healthz", response_model=HealthOut, tags=["health"])
    async def health():
        config = _state.config
        conn = _state.conn
        ok = True
        count = 0
        try:
            count = await db.run_in_threadpool(db.count_packages, conn)
        except Exception:
            ok = False
        return HealthOut(status="ok" if ok else "degraded", packages_count=count, db_ok=ok)

    # ---- Subscriber API ----

    @app.get("/v1/packages", response_model=PackageList, tags=["subscriber"])
    async def list_packages(
        limit: int = Query(20, ge=1, le=100),
        offset: int = Query(0, ge=0),
        _kp: str = Depends(require_api_key),
    ):
        await _transition_staged()
        config = _state.config
        pkgs = await db.run_in_threadpool(storage.list_packages, _state.conn, limit=limit, offset=offset, status="published")
        total = await db.run_in_threadpool(storage.count_packages, _state.conn, status="published")
        entries = [_entry(p, config) for p in pkgs]
        return PackageList(packages=entries, count=total, limit=limit, offset=offset)

    @app.get("/v1/packages/{pid}", response_model=PackageFull, tags=["subscriber"])
    async def fetch_package(pid: str, _kp: str = Depends(require_api_key)):
        await _transition_staged()
        config = _state.config
        pkg = await db.run_in_threadpool(storage.get_package, _state.conn, pid)
        if pkg is None or pkg["status"] != "published":
            raise HTTPException(status_code=404, detail={"error": {"code": "not_found", "message": "package not found"}})
        cits = await db.run_in_threadpool(storage.get_citations, _state.conn, pid)
        return _package_full(pkg, cits, config)

    @app.get("/v1/packages/{pid}/verify", response_model=VerifyOut, tags=["public"])
    async def verify_package(pid: str):
        """FR-022: content-hash verification; public per spec (no auth required)."""
        try:
            result = await db.run_in_threadpool(storage.recompute_content_hash, _state.conn, pid)
        except KeyError:
            raise HTTPException(status_code=404, detail={"error": {"code": "not_found", "message": "package not found"}})
        return VerifyOut(**result)

    # ---- Public API ----

    @app.get("/v1/catalog", response_model=Catalog, tags=["public"])
    async def catalog(
        limit: int = Query(20, ge=1, le=100),
        offset: int = Query(0, ge=0),
        _ip=Depends(ip_limit_catalog),
    ):
        await _transition_staged()
        config = _state.config
        pkgs = await db.run_in_threadpool(storage.list_packages, _state.conn, limit=limit, offset=offset, status="published")
        total = await db.run_in_threadpool(storage.count_packages, _state.conn, status="published")
        entries = [
            CatalogEntry(
                id=p["id"], question=p["question"], score=p["quality_score"],
                signature_summary=_sig_summary(p.get("signature") or {}),
                published_at=p.get("published_at"),
                article_url=f"{config.public_url}/articles/{p['slug']}",
            )
            for p in pkgs
        ]
        return Catalog(packages=entries, count=total, limit=limit, offset=offset)

    @app.get("/v1/sample", response_model=SampleOut, tags=["public"])
    async def sample(
        _ip=Depends(ip_limit),
        _ua=Depends(ua_limit),
    ):
        await _transition_staged()
        config = _state.config
        pkg = await db.run_in_threadpool(storage.get_sample, _state.conn)
        if pkg is None:
            raise HTTPException(status_code=404, detail={"error": {"code": "no_sample", "message": "no sample package configured"}})
        cits = await db.run_in_threadpool(storage.get_citations, _state.conn, pkg["id"])
        # FR-022: redacted sample shows at most 3 citation previews.
        previews = [
            CitationPreview(ordinal=c["ordinal"], title=c["title"], source_type=c["source_type"])
            for c in cits[:3]
        ]
        return SampleOut(
            id=pkg["id"], question=pkg["question"], score=pkg["quality_score"],
            signature_summary=_sig_summary(pkg.get("signature") or {}),
            summary=pkg.get("summary"),
            citation_previews=previews,
            article_url=f"{config.public_url}/articles/{pkg['slug']}",
            access_url=f"{config.public_url}/access",
        )

    # ---- OpenAPI (FR-020) ----

    @app.get("/v1/openapi.json", tags=["meta"])
    async def openapi_json():
        schema = app.openapi()
        config = _state.config
        if config and config.public_url:
            schema["servers"] = [{"url": config.public_url}]
        return JSONResponse(schema)

    # ---- HTML routes ----

    @app.get("/", response_class=HTMLResponse, tags=["html"])
    async def index_html():
        config = _state.config
        try:
            pkgs = await db.run_in_threadpool(storage.list_packages, _state.conn, limit=20, offset=0, status="published")
            count = await db.run_in_threadpool(db.count_packages, _state.conn)
            health = {"db_ok": True, "packages_count": count}
        except Exception:
            return HTMLResponse(render.render_503(config), status_code=503)
        return HTMLResponse(render.render_index(pkgs, config, health=health))

    @app.get("/articles/{slug}", response_class=HTMLResponse, tags=["html"])
    async def article_html(slug: str):
        config = _state.config
        try:
            pid = await db.run_in_threadpool(storage.get_package_id_by_slug, _state.conn, slug)
        except Exception:
            return HTMLResponse(render.render_503(config), status_code=503)
        if pid is None:
            raise HTTPException(status_code=404, detail={"error": {"code": "not_found", "message": "article not found"}})
        pkg = await db.run_in_threadpool(storage.get_package, _state.conn, pid)
        if pkg is None or pkg["status"] != "published":
            raise HTTPException(status_code=404, detail={"error": {"code": "not_found", "message": "article not found"}})
        cits = await db.run_in_threadpool(storage.get_citations, _state.conn, pkg["id"])
        return HTMLResponse(render.render_article(pkg, cits, config))

    @app.get("/access", response_class=HTMLResponse, tags=["html"])
    async def access_html():
        return HTMLResponse(render.render_access(_state.config))

    @app.get("/feed.xml", response_class=PlainTextResponse, tags=["html"])
    async def feed_xml():
        await _transition_staged()
        config = _state.config
        pkgs = await db.run_in_threadpool(storage.list_packages, _state.conn, limit=20, offset=0, status="published")
        return PlainTextResponse(render.render_feed(pkgs, config), media_type="application/atom+xml")


# Module-level app instance (constructed after all registrars are defined).
app = create_app(run_lifespan=True)
