# Self-hosting AgentNews

A paid, machine-readable feed of research bundles produced by Sneferu
convergence runs — fronted by a free public newsroom that proves bundle
quality to humans.

AgentNews ingests converged research runs from a Sneferu `runs/` directory,
applies a structural admission screen (signature integrity, quality score,
citation verification), persists each bundle in SQLite, and serves a versioned
JSON REST API plus an Atom feed and a server-rendered article index. A buyer's
agent authenticates with an API key, pulls one bundle over HTTP, and produces a
derivative article whose every cited link is independently verifiable.

**Product guide:** [`USER_GUIDE.md`](USER_GUIDE.md) — for the person
using the delivered product (reading articles, buying API access, pulling
bundles with an agent).

---

## What you get

- A **public newsroom** (`/`) listing published research bundles as
  human-readable articles, an Atom feed (`/feed.xml`), and a public catalog
  (`/v1/catalog`) and free sample (`/v1/sample`) — no account required.
- A **paid REST API** (`/v1/packages`) returning full machine-readable bundles
  (question, findings, verified citations, methodology notes, cross-vendor
  agreement signature, reuse license) to authenticated subscribers.
- A **CLI** (`agentnews`) for importing Sneferu runs, managing API keys,
  curating the sample, withholding/republishing, generating a static site,
  and operating the service.

## Why use it

You run a self-publishing writing agent that currently assembles source
material by hand before each publishing cycle. AgentNews gives your agent a
structured research bundle it can rewrite from directly — with a verified
reference list and a machine-readable reuse license — so every cited link in
the derivative article resolves to a real source and the cross-vendor
agreement certificate is quotable as evidence of rigor.

## Quick start

### Prerequisites

- Python **3.9+**
- `pip`
- A directory of Sneferu converged run outputs (one run per subdirectory, each
  containing `signature.json`, `builder_packet/artifact.md`, and
  `problem_statement.md`)

### Install

```bash
pip install -e ".[dev]"
```

The package uses a `src/` layout; the editable install exposes the
`agentnews` console script and the `agentnews.api:app` ASGI target.

### Configure

```bash
cp .env.example .env
# Edit .env — set the four values below, keep the rest at defaults for now:
#   PUBLIC_URL          https://news.example.com   (must be https://)
#   PAYMENT_LINK_URL    https://buy.example.com/agentnews
#   ADMIN_TOKEN         a long random secret
#   SNEFERU_RUNS_DIR    /absolute/path/to/sneferu/runs
python3 -m agentnews init --env-file .env
```

`init` validates the required env vars, generates `LOG_SALT` (used to salt
hashed IP/User-Agent values in the request log) if absent, and leaves an
existing `.env` untouched.

### Import a run and serve

```bash
# 1. Run database migrations (creates ./data/agentnews.db)
python3 -m agentnews migrate --env-file .env

# 2. Import a Sneferu run (publishes immediately when STAGING_DELAY_MINUTES=0)
python3 -m agentnews import-run "$SNEFERU_RUNS_DIR/2026-08-15T...-research-abc" \
    --skip-verify-urls --env-file .env

# 3. Mark that bundle as the single free sample (replace with the pid=... from step 2)
python3 -m agentnews sample set --env-file .env pkg-20260817-xxxxxx

# 4. Create a subscriber API key (plaintext shown once — save it)
python3 -m agentnews key create --label "first subscriber" --env-file .env

# 5. Start the server
python3 -m agentnews serve --env-file .env
```

### Expected result

The server boots and prints:

```
serving AgentNews on http://127.0.0.1:8000 (db=./data/agentnews.db)
INFO:     Uvicorn running on http://127.0.0.1:8000
```

### Proof step — verify it works

Open these in a browser or with `curl` against the running server:

| Check | Command | Pass |
|-------|---------|------|
| Health | `curl -s http://127.0.0.1:8000/healthz` | `{"status":"ok","packages_count":N,"db_ok":true}` |
| Newsroom | `curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8000/` | `200` |
| Public catalog | `curl -s http://127.0.0.1:8000/v1/catalog` | JSON with your bundle |
| Free sample | `curl -s http://127.0.0.1:8000/v1/sample` | Redacted sample JSON |
| Paid API (no key) | `curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8000/v1/packages` | `401` |
| Paid API (with key) | `curl -s -H "X-API-Key: ak_..." http://127.0.0.1:8000/v1/packages` | `200` + full bundle |

### Stop

Press `Ctrl+C` in the terminal running `serve`.

## Common next steps

- Browse the full API surface → [`docs/API.md`](API.md)
- Understand the system design → [`docs/ARCHITECTURE.md`](ARCHITECTURE.md)
- Operate, deploy, and debug → [`docs/OPERATIONS.md`](OPERATIONS.md)
- See the public web surface → [`docs/UI.md`](UI.md)
- Use the product as an end user → [`docs/USER_GUIDE.md`](USER_GUIDE.md)
- Build, test, and contribute → [`docs/DEVELOPMENT.md`](DEVELOPMENT.md)
- Review the security posture → [`docs/SECURITY.md`](SECURITY.md)
- Run the worked examples → [`examples/`](../examples/)

## Common errors

| Symptom | Cause / fix |
|---------|------------|
| `ERROR:MISSING_ENV:PUBLIC_URL` (or `PAYMENT_LINK_URL`, `ADMIN_TOKEN`) | A required env var is unset. Edit `.env` and re-run `init`. |
| `ERROR:INSECURE_PUBLIC_URL:<value>` | `PUBLIC_URL` must start with `https://`. Set `AGENTNEWS_ALLOW_INSECURE=true` only for local dev. |
| `address already in use` on `serve` | Another process holds the port. Set `AGENTNEWS_PORT=8001` in `.env` or stop the other process. |
| `no such table: packages` | Run `migrate` before importing or serving. |
| `401 Unauthorized` on `/v1/packages` | Pass the API key: `X-API-Key: ak_…`. |
| `ERROR:ADMIN_TOKEN_REQUIRED` | A protected CLI command (`--force`, `key`, `withhold`, `republish`, `sample set`) needs `ADMIN_TOKEN` in `.env`. |
| `REJECT:LOW_SCORE:...` on import | The run's quality score is below `PUBLISHABLE_SCORE_THRESHOLD` (default 60). Re-run with `--force` (requires `ADMIN_TOKEN`) only if you accept the risk. |

## Tests

```bash
python3 -m pytest tests/ -q
```

Tests covering: the ingest parser, the admission filter, storage and the
status machine, HTML/Atom rendering and sanitizer, every API route, the CLI,
auth, rate limiting, security headers, package-ID generation, and static-site
output layout.

## License

Subscriber reuse terms are in
[`src/agentnews/static/reuse-license-v1.md`](../src/agentnews/static/reuse-license-v1.md)
(served live at `/static/reuse-license-v1.md`). Project license is in
[`LICENSE.md`](../LICENSE.md).
