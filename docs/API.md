# API.md — AgentNews HTTP API and CLI reference

Audience: a frontend/integration developer wiring a writing agent to the paid
API, or an operator using the CLI. Every route below is read from
`src/agentnews/api.py`; every CLI command from `src/agentnews/cli.py`.

For product usage see [`USER_GUIDE.md`](USER_GUIDE.md); for system design see
[`ARCHITECTURE.md`](ARCHITECTURE.md); for worked examples see
[`../examples/`](../examples/).

---

## 1. Base URL

```
http://127.0.0.1:8000        # local dev (AGENTNEWS_HOST/AGENTNEWS_PORT)
https://news.example.com     # production behind nginx
```

The server stamps `servers` in the OpenAPI document from `PUBLIC_URL`
(`GET /v1/openapi.json`).

## 2. Authentication model

AgentNews uses **API keys** sent in the `X-API-Key` header. There is no OAuth,
no session, no cookie.

| Header | Form | Example |
|--------|------|---------|
| `X-API-Key` | `<key>` | `X-API-Key: ak_024d9ce6cf23af8caadf1f9352cda6f3` |

Key format is `ak_` followed by 32 lowercase hex characters. Keys are SHA-256
hashed at rest; the plaintext is shown **once** at creation by `agentnews key
create`. Validation results are cached in-memory with a TTL; revocation is honored
on the next cache miss.

Only `/v1/packages` and `/v1/packages/{pid}` require a key. The verify,
catalog, sample, health, OpenAPI, HTML, and feed routes are public. **There
are no admin HTTP endpoints** — sample curation, withhold/republish, key
management, and forced imports are CLI-only and require `ADMIN_TOKEN`.

## 3. Error envelope

All API errors return a flat JSON body (stack traces and internal paths are
never exposed):

```json
{ "error": { "code": "not_found", "message": "package not found" } }
```

| HTTP | `code` | Meaning |
|------|--------|---------|
| 401 | `unauthorized` | Missing `X-API-Key`. |
| 401 | `key_revoked_or_expired` | Key revoked or past `expires_at`. |
| 404 | `not_found` | Package id does not exist (JSON on API paths, HTML page on `/articles/<slug>`). |
| 404 | `no_sample` | No package is marked as the sample. |
| 429 | `rate_limited` | Rate limit exceeded; a `Retry-After: <seconds>` header is present. |
| 503 | (HTML) | Service starting up or DB unavailable. |

Every response (success and error) carries `X-Request-Id`, `X-Response-Time-Ms`,
and the security headers described in [`OPERATIONS.md`](OPERATIONS.md) §6.
Cacheable GET responses carry an `ETag`.

## 4. Rate limits

| Scope | Limit | Endpoints |
|-------|-------|-----------|
| Per API key | `RATE_LIMIT_PER_DAY` (default 100) + `BURST_ALLOWANCE` (default 20) per rolling 24h | `/v1/packages`, `/v1/packages/{pid}` |
| Per IP | 60 / day | `/v1/catalog` |
| Per IP | 10 / day | `/v1/sample` |
| Per User-Agent hash | 10 / day | `/v1/sample` |

On a 429 the response includes `Retry-After: <seconds>`. Successful
rate-limited responses also carry `X-RateLimit-Remaining` /
`X-RateLimit-Ip-Remaining` headers. State is in-memory and rebuilt from
`request_log` on restart.

## 5. HTTP routes

### 5.1 `GET /health` and `GET /healthz`

Health check. Public.

**Response 200** — `application/json`:

```json
{
  "status": "ok",
  "packages_count": 1,
  "db_ok": true
}
```

```bash
curl -s http://127.0.0.1:8000/healthz
```

### 5.2 `GET /v1/packages` — list published bundles (subscriber)

List all `published` packages. Authenticated + per-key rate limited.

| Query | Type | Default | Constraint |
|-------|------|---------|------------|
| `limit` | int | 20 | 1 ≤ limit ≤ 100 |
| `offset` | int | 0 | ≥ 0 |

**Response 200** — `PackageList`:

```json
{
  "schema": "agentnews.package_list/v1",
  "packages": [
    {
      "id": "pkg-20260817-d641f7",
      "question": "Should agent teams use sub-agents for parallel research sweeps?",
      "score": 78.5,
      "signature_summary": {
        "model_families": ["openai", "anthropic", "fireworks"],
        "cross_vendor": true,
        "degraded": false
      },
      "published_at": "2026-08-17T11:48:04Z",
      "article_url": "https://news.example.com/articles/pkg-20260817-d641f7",
      "latency_disclosure": "Converged in 24.0 hours."
    }
  ],
  "count": 1,
  "limit": 20,
  "offset": 0
}
```

```bash
curl -s -H "X-API-Key: ak_024d9ce6cf23af8caadf1f9352cda6f3" \
     "http://127.0.0.1:8000/v1/packages?limit=10"
```

### 5.3 `GET /v1/packages/{pid}` — fetch a full bundle (subscriber)

Return one package with citations, signature, reuse terms, and extensions.
Authenticated + per-key rate limited.

| Path | Type | Required |
|------|------|----------|
| `pid` | string | yes — e.g. `pkg-20260817-d641f7` |

**Response 200** — `PackageFull`:

```json
{
  "schema": "agentnews.package/v1",
  "id": "pkg-20260817-d641f7",
  "run_id": "run-good",
  "run_type": "unknown",
  "question": "Should agent teams use sub-agents for parallel research sweeps?",
  "summary": "Parallel sub-agent sweeps produce higher-quality synthesis...",
  "findings": "# Parallel sub-agent research sweeps\n\n## Findings\n...",
  "method_notes": "Evidence was gathered ... scored by a cross-vendor panel ...",
  "score": 78.5,
  "signature": {
    "degraded": false,
    "model_families": ["openai", "anthropic", "fireworks"],
    "cross_vendor": true,
    "convergence_score": 78.5,
    "started_at": "2026-08-15T10:00:00Z",
    "converged_at": "2026-08-16T10:00:00Z"
  },
  "reuse_terms": {
    "license": "agentnews-research-v1",
    "attribution_required": true,
    "may_republish": true,
    "derivative_allowed": true,
    "commercial_allowed": true,
    "max_excerpt_words": 300,
    "terms_version": "1.0",
    "terms_url": "",
    "disclaimer": "",
    "generated_at": ""
  },
  "citations": [
    {
      "ordinal": 1,
      "source_type": "web",
      "title": "Parallel multi-agent evidence gathering",
      "url": "https://example.com/paper-1",
      "authors": "Smith, Lee",
      "year": "2024",
      "verified": 1,
      "excerpt": "Parallel dispatch of independent agents ...",
      "accessed_at": "2026-08-15T12:00:00Z"
    }
  ],
  "content_hash": "sha256:c6f90fa1fd4f904319ed12a3b91ba8066da83ad4d3fb5b0e70e7a59e11b57d21",
  "article_url": "https://news.example.com/articles/pkg-20260817-d641f7",
  "latency_disclosure": "Converged in 24.0 hours.",
  "extensions": {},
  "created_at": "2026-08-17 11:47:50",
  "run_started_at": "2026-08-15T10:00:00Z",
  "run_completed_at": "2026-08-16T10:00:00Z",
  "published_at": "2026-08-17T11:48:04Z"
}
```

**`citations[].verified` states:** `1` resolved (HTTP 2xx/3xx), `-1`
restricted/bot-blocked (401/403/429), `-2` failed (404/5xx/timeout/DNS), `-3`
identity mismatch (the link resolves, but an arXiv abstract page names a
different paper than the citation does), `0` unchecked (default, or when
imported with `--skip-verify-urls`). `1` and `-1` both count toward
`CITATION_MINIMUM`; `-2` and `-3` never do.

**`extensions`** is nested JSON: `confidence_map`, `cost`, and
`corpus_manifest` keys are present when the source Sneferu run provided the
corresponding optional files.

```bash
curl -s -H "X-API-Key: ak_024d9ce6cf23af8caadf1f9352cda6f3" \
     http://127.0.0.1:8000/v1/packages/pkg-20260817-d641f7
```

### 5.4 `GET /v1/packages/{pid}/verify` — tamper check (public)

Recompute the content hash from the canonical payload and compare to the
stored value. **No authentication required.** This is post-import tamper
detection, not independent provenance proof.

**Response 200** — `VerifyOut`:

```json
{
  "package_id": "pkg-20260817-d641f7",
  "content_hash_stored": "sha256:c6f90fa1...",
  "content_hash_recomputed": "sha256:c6f90fa1...",
  "match": true,
  "signature": { "converged_at": "...", "cross_vendor": true, ... },
  "verified_at": "2026-08-17T11:48:41Z",
  "verification_scope": "post_import_tamper_detection"
}
```

A `match: false` means the stored bundle was altered after import. The
`signature` block is echoed back so the caller can re-confirm the agreement
certificate without a separate authenticated call.

```bash
curl -s http://127.0.0.1:8000/v1/packages/pkg-20260817-d641f7/verify
```

### 5.5 `GET /v1/catalog` — public catalog

List `published` packages with question, score, signature summary, and
article URL. Public, per-IP rate limited (60/day).

| Query | Type | Default | Constraint |
|-------|------|---------|------------|
| `limit` | int | 20 | 1 ≤ limit ≤ 100 |
| `offset` | int | 0 | ≥ 0 |

**Response 200** — `Catalog`:

```json
{
  "schema": "agentnews.catalog/v1",
  "packages": [
    {
      "id": "pkg-20260817-d641f7",
      "question": "Should agent teams use sub-agents for parallel research sweeps?",
      "score": 78.5,
      "signature_summary": { "model_families": [...], "cross_vendor": true, "degraded": false },
      "published_at": "2026-08-17T11:48:04Z",
      "article_url": "https://news.example.com/articles/pkg-20260817-d641f7"
    }
  ],
  "count": 1,
  "limit": 20,
  "offset": 0
}
```

```bash
curl -s "http://127.0.0.1:8000/v1/catalog?limit=5"
```

### 5.6 `GET /v1/sample` — free redacted sample

Return the single package marked `is_sample=1`, with at most 3 citation
previews (no excerpts, no URLs). Public, per-IP (10/day) + per-UA (10/day)
rate limited.

**Response 200** — `SampleOut`:

```json
{
  "schema": "agentnews.sample/v1",
  "id": "pkg-20260817-d641f7",
  "question": "Should agent teams use sub-agents for parallel research sweeps?",
  "score": 78.5,
  "signature_summary": { "model_families": [...], "cross_vendor": true, "degraded": false },
  "summary": "Parallel sub-agent sweeps produce higher-quality synthesis...",
  "citation_previews": [
    { "ordinal": 1, "title": "Parallel multi-agent evidence gathering", "source_type": "web" }
  ],
  "article_url": "https://news.example.com/articles/pkg-20260817-d641f7",
  "access_url": "https://news.example.com/access"
}
```

**Response 404** `{"error":{"code":"no_sample","message":"no sample package configured"}}`
when no package is marked as the sample.

```bash
curl -s http://127.0.0.1:8000/v1/sample
```

### 5.7 `GET /v1/openapi.json` — OpenAPI schema

The FastAPI-generated OpenAPI document, with `servers` set to `PUBLIC_URL`.
Public.

```bash
curl -s http://127.0.0.1:8000/v1/openapi.json | python3 -m json.tool | head -30
```

### 5.8 HTML and feed routes (public)

| Route | Type | Purpose |
|-------|------|---------|
| `GET /` | `text/html` | Newsroom index — list of published articles. |
| `GET /articles/{slug}` | `text/html` | Full article page for one bundle. `slug` equals the package id. Returns a styled 404 HTML page for an unknown/withheld slug. |
| `GET /access` | `text/html` | API access / payment page; links to `PAYMENT_LINK_URL` and the no-key evaluation endpoints. |
| `GET /feed.xml` | `application/atom+xml` | Atom feed of the latest 20 published packages. |
| `GET /static/{file}` | varies | Static assets: `style.css`, `mark.svg`, `reuse-license-v1.md`, `preview-bootstrap.js`. |
| `GET /articles/{nonexistent}` | `text/html` 404 | HTML not-found page for browser routes. |

## 6. CLI reference

All commands are invoked as `agentnews <command> [options]` or
`python3 -m agentnews <command>`. Every command accepts `--env-file <path>`
(default `.env`). Exit codes: `0` OK, `2` ERROR (bad input), `3` REJECT
(admission filter), `4` STORE error.

### 6.1 Lifecycle

| Command | Purpose | Key flags |
|---------|---------|-----------|
| `init` | Validate required env vars; generate `LOG_SALT` if absent. | — |
| `migrate` | Run pending forward-only SQL migrations. | — |
| `serve` | Run the ASGI server (uvicorn). | `--port`, `--log-level` |
| `deploy-static` | Render a frozen static site to `STATIC_OUTPUT_DIR`. | `--output <dir>` |
| `backup --to <dir>` | Checkpoint WAL and copy the DB to a timestamped file in `<dir>`. | Positional `<path>` also accepted |
| `restore <path>` | Restore the DB from a backup file. | — |
| `purge-logs` | Delete `request_log` rows older than N days. | `--days N` (default 30) |

### 6.2 Import and curation

| Command | Purpose | Key flags |
|---------|---------|-----------|
| `import-run <run_dir>` | Parse + verify + filter + store one Sneferu run. | `--stage`, `--sample`, `--skip-verify-urls`, `--dry-run`, `--force`, `--replace`, `--show-hash` |
| `scan` | List Sneferu run dirs under `SNEFERU_RUNS_DIR` with import status. | `--runs-dir <dir>` |
| `watch` | Poll `SNEFERU_RUNS_DIR` and auto-import new runs (lockfile + dedup). | `--dir <dir>` (also `--runs-dir`), `--interval <s>`, `--skip-verify-urls`, `--deploy-static-after-import` |
| `validate-sneferu-layout <run_dir>` | Print a compatibility report for a run dir without importing. | — |
| `list` | List all packages in the DB (id, status, score, question). | — |
| `show <pid>` | Print full package details as JSON. | — |
| `withhold <pid>` | Move a published package to `withheld`. Requires `ADMIN_TOKEN`. | — |
| `republish <pid>` | Move a withheld package back to `published`. Requires `ADMIN_TOKEN`. | — |

`import-run` flag semantics:

- `--stage`: land as `staged` with `publish_after` = now + `STAGING_DELAY_MINUTES`
  (lazy transition to `published` happens on the next API read after that time).
- `--sample`: mark the imported package as the single sample (replaces any
  existing sample).
- `--skip-verify-urls`: skip the pre-transaction URL verification (sets all
  `verified=0`); useful when offline. The admission filter still runs.
- `--dry-run`: parse + verify + filter, print a JSON verdict, write nothing.
- `--force`: bypass a REJECT verdict and import anyway. **Requires
  `ADMIN_TOKEN`.** Does not bypass ERROR-class failures (missing files,
  malformed JSON).
- `--replace`: delete an existing package with the same `run_id` and re-import
  **preserving the original package id**.
- `--show-hash`: print the computed `content_hash` alongside the import
  verdict.

Example with admission rejection:

```bash
$ python3 -m agentnews import-run /path/to/run-bad --env-file .env
REJECT:LOW_SCORE:42.0
EXIT 3
```

Example forcing a rejected run through (requires `ADMIN_TOKEN`):

```bash
python3 -m agentnews import-run /path/to/run-bad --force --env-file .env
```

### 6.3 Sample curation

| Command | Purpose |
|---------|---------|
| `sample set <pid>` | Mark `<pid>` as the single sample (replaces any existing). Requires `ADMIN_TOKEN`. |
| `sample show` | Print the current sample package id, or "no sample". |
| `sample unset <pid>` | Remove the sample flag from `<pid>`. Requires `ADMIN_TOKEN`. |

### 6.4 API key management

| Command | Purpose |
|---------|---------|
| `key create [--label <text>] [--days <N>]` | Generate a new subscriber key. Prints `key_id`, `key=` (plaintext, once), `prefix=`, `expires_at`. Requires `ADMIN_TOKEN`. |
| `key revoke <ident>` | Revoke a key by id or prefix. Requires `ADMIN_TOKEN`. |
| `key list` | Print all keys (id, prefix, label, status, created_at, expires_at, revoked_at) as JSON. Requires `ADMIN_TOKEN`. |

Example:

```bash
$ python3 -m agentnews key create --label "first subscriber" --env-file .env
key_id=1
key=ak_024d9ce6cf23af8caadf1f9352cda6f3
prefix=ak_024d9
expires_at=2026-11-15T12:00:00Z
Store this key securely; it is shown only once.
```

### 6.5 Verification and reports

| Command | Purpose |
|---------|---------|
| `verify <pid>` | Recompute the content hash and print `{match, ...}`. Exit `1` if `match` is false. |
| `report usage` | Request-log usage broken down by key prefix and endpoint. |
| `report costs [--days N]` | Imported-run cost totals over the lookback window. |

`report usage` output (truncated):

```json
{
  "schema": "agentnews.usage_report/v1",
  "days": 30,
  "total_authenticated_requests": 4,
  "by_key": {
    "ak_024d9": { "requests": 4, "endpoints": { "/v1/packages": { "count": 2, "avg_response_ms": 1.0 } } }
  }
}
```

`report costs` output:

```json
{
  "schema": "agentnews.costs_report/v1",
  "days": 30,
  "imports_with_cost_data": 1,
  "total_usd": 12.4,
  "generated_at": "2026-08-17T11:48:52Z"
}
```

## 7. Python library surface

The package is importable as `agentnews`. The public, stable-for-integration
symbols are:

| Symbol | Module | Purpose |
|--------|--------|---------|
| `agentnews.api:app` | `src/agentnews/api.py` | The ASGI application (for `uvicorn` or any ASGI server). |
| `agentnews.api:create_app(config=, conn=, run_lifespan=)` | `src/agentnews/api.py` | Construct a fresh app for testing/embedding; pass an existing `Config` and `sqlite3.Connection` and `run_lifespan=False` to skip DB bootstrap. |
| `agentnews.config:load_config(env_path=".env")` | `src/agentnews/config.py` | Load `.env` + env vars into a `Config`. Does not validate. |
| `agentnews.config:validate_required_env(config)` | `src/agentnews/config.py` | Raise `EnvValidationError` if required vars are missing/invalid. |
| `agentnews.cli:main(argv=None)` | `src/agentnews/cli.py` | The CLI entrypoint; returns the exit code. |
| `agentnews.ingest:parse_run(run_dir, config)` | `src/agentnews/ingest.py` | Parse a Sneferu run dir into a `ParsedRun`; raises `IngestError`. |
| `agentnews.filter:admit(parsed, config, skip_verify=False)` | `src/agentnews/filter.py` | Run URL verification + admission; returns `(admitted, reject_codes)`. |
| `agentnews.storage:insert_package(conn, parsed, config, ...)` | `src/agentnews/storage.py` | Persist a `ParsedRun`; raises `DuplicateRunError`. |
| `agentnews.auth:generate_key()` | `src/agentnews/auth.py` | Return `(plaintext, key_hash, prefix)`. |
| `agentnews.auth:validate_key(key, conn, cache)` | `src/agentnews/auth.py` | Return `(valid, reason)`. |

The Pydantic response models in `agentnews.models` (`PackageFull`,
`PackageList`, `Catalog`, `SampleOut`, `VerifyOut`, `HealthOut`, and their
entry/citation submodels) are the wire contract and may be imported for typed
client code.

## 8. Public API summary table

| Method | Path | Auth | Rate limit | Purpose |
|--------|------|------|-----------|---------|
| GET | `/health` `/healthz` | none | none | Health check |
| GET | `/v1/packages` | API key | per-key | List published bundles |
| GET | `/v1/packages/{pid}` | API key | per-key | Fetch one full bundle |
| GET | `/v1/packages/{pid}/verify` | none | none | Tamper-detection hash check |
| GET | `/v1/catalog` | none | IP 60/day | Public catalog |
| GET | `/v1/sample` | none | IP 10/day + UA 10/day | Free redacted sample |
| GET | `/v1/openapi.json` | none | none | OpenAPI schema |
| GET | `/` | none | none | Newsroom index (HTML) |
| GET | `/articles/{slug}` | none | none | Article page (HTML) |
| GET | `/access` | none | none | Access/payment page (HTML) |
| GET | `/feed.xml` | none | none | Atom feed |
| GET | `/static/{file}` | none | none | Static assets |
