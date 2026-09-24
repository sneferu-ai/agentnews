# USER_GUIDE.md — AgentNews for the end user

This guide is for the person **using** AgentNews: reading the public
newsroom, buying API access, and pulling research bundles with a writing
agent to produce derivative articles. It is not a maintainer reference — for
installing, operating, and debugging the service see
[`../README.md`](../README.md) and [`OPERATIONS.md`](OPERATIONS.md).

---

## 1. Onboarding — the first URL to open

AgentNews runs as a website with a JSON API behind it. Your operator gives you
one URL: the public newsroom root.

```
https://news.example.com/
```

(Replace `news.example.com` with your operator's real domain. On a local
install the address is `http://127.0.0.1:8000/`.)

Open it in any browser. The page that loads is the **newsroom index**: a list
of published research bundles rendered as articles, each showing its research
question, a quality score badge, the model families that produced it, and a
publication date. No account is required to read this page.

## 2. Access and login — how authentication works

AgentNews has **two tiers of access**, and understanding which one you are in
determines what you can do:

| Tier | What you see | How you get in |
|------|--------------|----------------|
| **Public (no login)** | Newsroom articles, the public catalog, one free sample bundle, the Atom feed, the OpenAPI schema, the reuse license | Just open the URL. No account, no key. |
| **Subscriber (API key)** | Full machine-readable bundles via `/v1/packages` — every citation, methodology notes, the full signature, the reuse terms | An API key the operator issues you. There is no signup form in the product itself. |

There is **no password login, no session, no cookie, no "Sign in" button**.
The website is fully public. The paid product is the JSON API, and the only
credential is an API key string.

### Getting an API key

1. Pay for access at the link on the **Access** page
   (`https://news.example.com/access`).
2. The operator issues you an API key in the form `ak_` followed by 32 hex
   characters, for example:

   ```
   ak_024d9ce6cf23af8caadf1f9352cda6f3
   ```

3. Save it somewhere secure. The operator cannot recover the full key after
   creation — only the first 8 characters (`ak_024d9`) are stored and shown
   in records. Treat it like a password.

The key is valid for a fixed number of days from issue (default 90 days; your
operator sets the exact window). After it expires, request a new key from the
operator.

### Development / local credentials

If you are running a local install for development, the operator (you) creates
keys with the CLI:

```bash
python3 -m agentnews key create --label "local dev" --env-file .env
```

The plaintext key is printed **once**. There are no shipped demo credentials.

## 3. The primary user journey — buy access, pull a bundle, write an article

This is the complete journey from first visit to a saved, published derivative
article. Numbered so you can follow it end to end.

### Step 1 — Read the public newsroom to find a bundle

Open `https://news.example.com/`. Skim the article list. Each card links to a
full article page at `https://news.example.com/articles/<slug>` where `<slug>`
is the bundle's package ID (e.g. `pkg-20260817-d641f7`). The article page
shows the research question, the synthesized findings, the reference list
with verification badges, the cross-vendor agreement certificate, and the
reuse-license summary. No key is needed for this.

### Step 2 — Evaluate before buying: use the free sample and catalog

Before paying, confirm the bundle shape is what your agent needs:

```bash
# Public catalog — every published bundle's question, score, and signature
curl -s https://news.example.com/v1/catalog | python3 -m json.tool
```

```bash
# Free sample — one redacted bundle (at most 3 citation previews)
curl -s https://news.example.com/v1/sample | python3 -m json.tool
```

The sample is real data with the citation excerpts and most citation URLs
removed. The catalog lists every bundle with its `article_url` so you can read
the human version before deciding to buy.

> **Worked example:** `examples/fetch_catalog.py` automates these two curl
calls and prints a readable summary without an API key.

### Step 3 — Buy access and receive an API key

Open `https://news.example.com/access`. It explains the offering, links to the
payment page (`PAYMENT_LINK_URL`), and lists the no-key evaluation endpoints.
Complete payment on the linked page. The operator issues you an API key (see
§2). The founding price is a flat one-time payment for 90-day unlimited
access (default $100 USD; your operator sets the real price).

### Step 4 — Pull a full bundle with your API key

Authenticate by sending the key in the `X-API-Key` header. List the bundles you now have paid access to:

```bash
curl -s -H "X-API-Key: ak_024d9ce6cf23af8caadf1f9352cda6f3" \
     https://news.example.com/v1/packages | python3 -m json.tool
```

Pick a package ID from the response and fetch the full bundle:

```bash
curl -s -H "X-API-Key: ak_024d9ce6cf23af8caadf1f9352cda6f3" \
     https://news.example.com/v1/packages/pkg-20260817-d641f7 \
     | python3 -m json.tool
```

> **Worked example:** `examples/fetch_bundle.py` lists the paid packages,
> picks the first one, fetches the full bundle, and prints the question,
> score, citation verification states, and the cross-vendor agreement
> certificate.

The response is a complete machine-readable bundle containing:

- `question` — the originating research question
- `summary` — a one-paragraph synthesis
- `findings` — the full Markdown findings
- `citations` — the reference list, each with `verified` state, `url`,
  `title`, `authors`, `year`, and `excerpt`
- `method_notes` — methodology disclosure
- `signature` — the cross-vendor agreement certificate (model families,
  `cross_vendor` flag, `degraded` flag, convergence score, timestamps)
- `reuse_terms` — the machine-readable reuse license (what you may republish,
  attribution requirement, excerpt word limit)
- `extensions` — nested JSON carrying optional `confidence_map`, `cost`, and
  `corpus_manifest` data when the source run provided them

### Step 5 — Verify the bundle has not been tampered with

Independent of the operator, recompute and compare the bundle's content hash:

```bash
curl -s https://news.example.com/v1/packages/pkg-20260817-d641f7/verify \
     | python3 -m json.tool
```

The `verify` endpoint is **public** (no key required). It returns the stored
hash, a freshly recomputed hash, and `match: true/false`. A `match: false`
means the stored bundle was altered after import — do not publish from it and
contact the operator. This is post-import tamper detection, not independent
provenance proof: it confirms the bytes you fetch match the bytes that were
admitted, computed from the canonical (question, findings, citations,
method_notes, score) payload.

### Step 6 — Hand the bundle to your writing agent and produce the article

Your agent reads the JSON, rewrites the findings in your voice, cites the
references (each citation carries a `verified` state and a resolvable `url`),
and may quote the cross-vendor agreement certificate as evidence of rigor.
Respect `reuse_terms`: by default, attribution is required, republishing the
synthesized package is permitted, and at most `excerpt_limit_words` (default
300) words from a single package may appear in any single downstream work
without a separate written agreement. Third-party sources cited inside a
bundle are **not** covered by the AgentNews reuse license — your agent must
verify each cited source's own licensing before republishing it.

### Step 7 — Save and publish your derivative article

Save the produced article wherever your publishing pipeline expects it. The
visible, saved outcome is your published article whose every cited link
resolves to a real source (you verified this in step 5 and your agent carried
the `verified` citations forward). AgentNews does not host your derivative
article — it supplies the research bundle your agent rewrote from.

## 4. In-product help and the guide

AgentNews is a **no-JavaScript** product. There is no in-app help widget,
search overlay, or interactive tutorial. Help is delivered as:

- **The Access page** (`/access`) — always-available explanation of the
  offering, the evaluation endpoints, and the payment link. Read this first
  if you are unsure how to buy.
- **The reuse license** (`/static/reuse-license-v1.md`) — the full text of what
  you may republish, linked from every article footer and the Access page.
- **The OpenAPI schema** (`/v1/openapi.json`) — the machine-readable contract
  for every API endpoint, useful if your agent is integrating directly.
- **This guide** — keep the link your operator gave you.

## 5. Restart and recovery

### The server restarted — how do I get back to where I was?

Your API key is the only thing you need. There is no session to restore and
no local state in the product. After a restart:

1. Open `https://news.example.com/` in your browser — the newsroom loads
   again from the database. Published articles are unchanged.
2. Re-run any `curl` command from §3 using your saved API key. Bundles are
   persisted in the operator's SQLite database and survive restarts.

### I lost my API key

The operator cannot recover the full key (only the first 8 characters are
stored). Request a new key from the operator; the old one can be revoked so it
cannot be used even if found.

### My key stopped working (401 `key_revoked_or_expired`)

Either the operator revoked it or it passed its expiry date. Request a new
key. If you believe this is an error, contact the operator — only the operator
can inspect or extend key validity.

### Verifying saved work after a restart

If you want independent confirmation that a bundle you previously pulled is
still intact, re-fetch it and compare, or call the public `verify` endpoint:

```bash
curl -s https://news.example.com/v1/packages/pkg-20260817-d641f7/verify
```

A `match: true` confirms the stored bytes are unchanged.

## 6. Common user-facing errors and exact recovery actions

| Error (what you see) | Where | Why | Exact recovery action |
|----------------------|-------|-----|-----------------------|
| `401` `{"error":{"code":"unauthorized","message":"missing X-API-Key"}}` | `curl` to `/v1/packages` | No key sent. | Add `-H "X-API-Key: ak_…"`. |
| `401` `{"error":{"code":"key_revoked_or_expired","message":"invalid or revoked key"}}` | `curl` to `/v1/packages` | Key revoked or past expiry. | Request a new key from the operator. |
| `429` `{"error":{"code":"rate_limited",...}}` with a `Retry-After: N` header | `curl` to any rate-limited endpoint | You exceeded the per-key daily limit (default 100/day + 20 burst) or the per-IP limit on `/v1/sample` (10/day) or `/v1/catalog` (60/day). | Wait `N` seconds (the `Retry-After` value) and retry. Space requests out. |
| `404` HTML page "Not found" | Browser on `/articles/<wrong-slug>` | The slug does not exist or was withheld. | Go back to `/` and pick a current article. Withheld bundles are removed from the public list. |
| `404` `{"error":{"code":"not_found","message":"package not found"}}` | `curl` to `/v1/packages/<wrong-id>` | Bad package ID. | List packages first: `curl -s -H "X-API-Key: ak_…" /v1/packages`. |
| `404` `{"error":{"code":"no_sample","message":"no sample package configured"}}` | `curl` to `/v1/sample` | The operator has not marked a sample. | This is an operator action, not a user fix. Read `/v1/catalog` instead, or ask the operator to set a sample. |
| `503` HTML "Service unavailable" page | Browser, any page | The service is starting up or the database is unavailable. | Wait a few seconds and reload. If it persists, the operator is aware (the health check drives restarts). |
| `{"match": false}` from `/v1/packages/<id>/verify` | `curl` to verify | The stored bundle was altered after import. | Do not publish from this bundle. Contact the operator. |

## 7. What is explicitly not in this product

- **No user accounts, no signup form, no password login.** Access is a single
  API key the operator issues after payment.
- **No in-app search, no filtering UI, no personalization.** The newsroom is a
  chronological list; the catalog is a paginated JSON list. Your agent does
  any filtering downstream.
- **No hosting of your derivative article.** AgentNews supplies the research
  bundle; you publish the article wherever you publish.
- **No guarantee that cited third-party sources are licensed for your reuse.**
  The AgentNews reuse license covers the synthesized package only. You must
  verify each cited source's own licensing before republishing it.
