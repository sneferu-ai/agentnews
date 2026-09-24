# Runtime Test Plan — AgentNews

This plan describes the runtime smoke tests the Sneferu runtime-test phase
executes against a live AgentNews server. The server is spawned by
`packaging.json`'s `runtime_test_command`:

    python3 scripts/runtime_serve.py {port}

The command seeds the database with the `tests/fixtures/run-good` bundle and
a deterministic API key before starting uvicorn.

Artifact type: `local_api`. Runtime test mode: `http`. Each numbered item
below is a discrete check the tester runs against the spawned HTTP service.
Item 1 is the primary boot-proof journey; the remaining items cover the
public surface, the paid subscriber gate, content integrity, security
headers, and the failure paths that distinguish a shipping service from a
skeleton.

## Preconditions

The runtime start command (`python3 scripts/runtime_serve.py {port}`)
seeds the database before launching the server: it imports one Sneferu run
from `tests/fixtures/run-good`, marks that bundle as the free sample, and
inserts a deterministic subscriber API key. The seeded package ID is fixed
at `pkg-runtime-test-0001` (so this plan can reference exact paths), and the
runtime-test API key is `ak_00000000000000000000000000000001`. The seed
mirrors the `tests/e2e_journey.py` CLI journey so the runtime evidence is
consistent with the static test suite (167 tests, all green).

## Primary journey

1. GET /healthz returns 200 with JSON body {"status":"ok","packages_count":N,"db_ok":true} — proves the server booted, the SQLite database is reachable, and migrations ran. This is the single most important check: no health, no further evidence counts.

## Public surface (no auth)

2. GET / returns 200 with text/html, the string "AgentNews" in the body, and <html lang="en"> — the public newsroom index renders server-side with no JavaScript.
3. GET /v1/catalog returns 200 with JSON body whose "schema" field equals "agentnews.catalog/v1", whose "count" is at least 1, and whose "packages" array contains an entry with "id" = "pkg-runtime-test-0001" — the public machine-readable catalog lists the seeded bundle.
4. GET /v1/sample returns 200 with JSON whose "id" equals "pkg-runtime-test-0001", containing a "question", a "summary", and a "citation_previews" array of length at most 3 — the redacted free sample proves bundle quality without leaking the full payload.
5. GET /feed.xml returns 200 with Content-Type application/atom+xml and a body containing "<feed" and "Atom" — the Atom feed is valid and syndicatable.
6. GET /access returns 200 with text/html containing the configured payment link host (buy.example.com) — the access/offer page is live.
7. GET /v1/openapi.json returns 200 with JSON containing an "openapi" key, a "paths" object that includes "/v1/packages", and a "servers" array whose first entry is the configured PUBLIC_URL — the machine-readable API contract is served.

## Paid subscriber gate

8. GET /v1/packages with no X-API-Key header returns 401 with JSON body {"error":{"code":"unauthorized",...}} — the paid API is gated; anonymous access is refused, not silently granted.
9. GET /v1/packages with header `X-API-Key: ak_00000000000000000000000000000001` returns 200 with JSON body whose "schema" field equals "agentnews.package_list/v1" and whose "packages" array contains the entry with "id" = "pkg-runtime-test-0001" — an authenticated subscriber retrieves the full list.
10. GET /v1/packages/pkg-runtime-test-0001 with header `X-API-Key: ak_00000000000000000000000000000001` returns 200 with JSON containing the full bundle: "schema" = "agentnews.package/v1", "citations" array (length 5 for the seeded run), "reuse_terms" object, "signature" object, and a "content_hash" starting with "sha256:" — the complete machine-readable research bundle is delivered.
11. GET /v1/packages/nonexistent-id with header `X-API-Key: ak_00000000000000000000000000000001` returns 404 with JSON {"error":{"code":"not_found",...}} and Content-Type application/json — API 404s stay JSON, never HTML.

## Content integrity

12. GET /v1/packages/pkg-runtime-test-0001/verify returns 200 with JSON {"match":true,"verification_scope":"post_import_tamper_detection"} — the public content-hash verification endpoint independently recomputes the hash and confirms the stored bundle has not been tampered with since import.

## HTML article rendering

13. GET /articles/pkg-runtime-test-0001 returns 200 with text/html containing the bundle's question text, a "Convergence certificate" panel, a "Reuse" section, the content_hash string, and a link to /v1/packages/pkg-runtime-test-0001/verify — the human-readable article page renders the full research story with a verifiable certificate.

## Failure paths

14. GET /articles/nonexistent-slug returns 404 with text/html (not JSON), containing "Page not found" and a visible link back to "/" with the text "Return to the newsroom" — browser-facing 404s are styled HTML, not bare JSON.
15. GET /v1/packages with header `X-API-Key: ak_00000000000000000000000000000001`, repeated until the per-key daily burst is exhausted, returns 429 with JSON {"error":{"code":"rate_limited",...}} and a "Retry-After" header with an integer value >= 1 — rate limiting protects the paid API and surfaces retry guidance.

## Security headers (every response)

16. Every response (sample /healthz, /, /v1/catalog) includes the security headers: X-Content-Type-Options: nosniff, X-Frame-Options: DENY, Referrer-Policy: no-referrer, Strict-Transport-Security with max-age >= 31536000, and Content-Security-Policy containing "script-src 'none'" and NOT containing "'unsafe-inline'" — the security posture is enforced at the middleware layer on every route, not just API routes.

## No JavaScript invariant

17. GET /, GET /access, and GET /articles/pkg-runtime-test-0001 each return HTML that does NOT contain the substring "<script" — the product ships zero JavaScript (FR-035); all interactivity is server-rendered or CSS-only (disclosure via <details>, copy via triple-click selection).

## Caching

18. GET /v1/catalog returns 200 with an ETag header; a second GET with If-None-Match set to that ETag returns 304 — conditional GET is supported for cacheable public endpoints, reducing bandwidth for repeat subscribers.

## Static assets

19. GET /static/style.css returns 200 with a CSS body (contains "--bg" or "css") — the server-rendered stylesheet is served from the mounted static directory.
20. GET /static/reuse-license-v1.md returns 200 — the subscriber reuse license is served live so buyers can read the terms before purchasing.
