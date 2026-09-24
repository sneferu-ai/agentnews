# DEVELOPMENT.md — build, test, and contribute to AgentNews

Audience: an engineer modifying the AgentNews source. For running it see
[`OPERATIONS.md`](OPERATIONS.md); for the HTTP/CLI interface see
[`API.md`](API.md); for system design see
[`ARCHITECTURE.md`](ARCHITECTURE.md); for the security posture see
[`SECURITY.md`](SECURITY.md).

---

## 1. Repo layout

```
.
├── src/agentnews/           # the package (API + CLI shared core)
│   ├── api.py               # FastAPI app: 12 routes, lifespan, middleware, auth
│   ├── cli.py               # 19 top-level subcommands (sample/key/report have sub-subs)
│   ├── config.py            # env + .env loading, Config dataclass, validation
│   ├── db.py                # SQLite connection, migrations, PID file, retry_on_lock
│   ├── models.py            # Pydantic v2 response models (the API contract)
│   ├── ingest.py            # Sneferu run-directory parser
│   ├── filter.py            # URL verification (pre-transaction) + admission screen
│   ├── storage.py           # ingest→DB bridge, package status machine, key store
│   ├── auth.py              # API key generation, hashing, validation cache
│   ├── ratelimit.py         # rolling-window rate limiting (per-key/IP/UA)
│   ├── render.py            # stdlib Markdown + allowlist HTML sanitizer + Jinja2
│   ├── migrations/          # forward-only SQL migrations with SHA-256 checksums
│   ├── templates/           # Jinja2 HTML + Atom XML templates (no JS)
│   └── static/              # style.css, mark.svg, reuse-license-v1.md
├── tests/                   # pytest suite (167 tests)
├── scripts/                 # deploy.sh, systemd unit, nginx config
├── setup.py                 # build + deps + CLI entrypoint
├── pytest.ini               # pytest testpaths config
├── README.md                # project overview + quickstart
├── .env.example             # environment template
└── docs/                    # this documentation set
```

The `agentnews` package is a **hybrid library + CLI + ASGI app**. The CLI and
the API share the same `config`, `db`, `storage`, `ingest`, `filter`, `auth`,
`ratelimit`, and `render` modules. There is no separate service layer — the
API routes call `storage` / `auth` / `ratelimit` directly, and the CLI does
the same. The single source of truth for every behavior is the package.

## 2. Install for development

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

`setup.py` declares the dev extras: `pytest`, `build`, `wheel`. Runtime
dependencies include `fastapi`, `uvicorn`, `httpx`, `jinja2`, and `pydantic`.
Python 3.9+ is required.

## 3. Environment setup

```bash
cp .env.example .env
# Minimum for dev:
#   PUBLIC_URL=http://127.0.0.1:8000
#   PAYMENT_LINK_URL=https://buy.example.com/agentnews
#   ADMIN_TOKEN=$(openssl rand -hex 32)
#   AGENTNEWS_ALLOW_INSECURE=true
#   SNEFERU_RUNS_DIR=/path/to/sneferu/runs  (only for scan/watch)
python3 -m agentnews init --env-file .env
python3 -m agentnews migrate --env-file .env
```

`init` validates required env vars and generates `LOG_SALT` if it is absent.
`migrate` applies forward-only SQL migrations.

## 4. Running the server and CLI

```bash
# Start the ASGI server
python3 -m agentnews serve --env-file .env
# expected: serving AgentNews on http://127.0.0.1:8000 (db=./data/agentnews.db)

# In another shell, exercise the CLI
python3 -m agentnews list --env-file .env
python3 -m agentnews key create --label "local dev" --env-file .env
python3 -m agentnews import-run tests/fixtures/runs/run-good --skip-verify-urls --env-file .env
```

## 5. Test suite

```bash
python3 -m pytest tests/ -q
# expected: 167 passed
```

The suite covers: config loading/validation, DB migrations and lock retry,
ingest parsing and content-hash computation, the 4-state URL verification and
admission filter, storage (insert, duplicate-detection, status machine, sample
slot, key management), auth (generation, hashing, validation, revocation,
expiry), rate limiting (per-key/IP/UA, burst, warm-from-log), render
(markdown, sanitizer, templates), and the full API surface (every route, auth
gating, error envelopes, ETag, security headers, 404 handling).

Tests use an in-memory or tmp-dir SQLite database; they do not touch
`./data/agentnews.db`. Fixture runs live under `tests/fixtures/runs/`.

### Narrow test runs

```bash
# One file
python3 -m pytest tests/test_api.py -q

# One test
python3 -m pytest tests/test_storage.py::test_insert_and_get -q

# By keyword
python3 -m pytest tests/ -k "rate_limit" -q
```

## 6. Code style and conventions

- **Python 3.9+.** Use stdlib features available on 3.9 (e.g. `dict` rather
  than `Dict` from `typing` where Pydantic accepts it). `from __future__ import
  annotations` is used so Pydantic v2 models can use newer annotation syntax while
  remaining compatible with Python 3.9.
- **Pydantic v2** for all response models (`models.py`). Models are the wire
  contract — change them deliberately.
- **No client JavaScript.** Every HTML page is server-rendered from Jinja2
  templates. The CSP enforces `script-src 'none'`.
- **Forward-only migrations.** Add a new numbered file under
  `migrations/` and update `db.py`'s migration list. Never edit an applied
  migration; the SHA-256 checksum will mismatch on hosts that already applied
  the old version.
- **Shared path/filename safety.** Use the existing primitives in
  `src/agentnews/` for any new path-handling code; do not concatenate paths
  from user input.
- **Error envelopes.** API errors return `{"error":{"code":..., "message":...}}`.
  Never include stack traces or internal paths in an API response.
- **Admin actions are CLI-only.** Do not add admin HTTP endpoints. New
  state-mutating operator commands go in `cli.py` and require `ADMIN_TOKEN`.

## 7. Adding a new endpoint

1. Add the Pydantic response model to `src/agentnews/models.py`.
2. Add the route to `src/agentnews/api.py`. Use the `require_api_key`
   dependency if it is subscriber-only; leave it off for public routes.
3. Add storage methods to `src/agentnews/storage.py` as needed (pure DB
   access, no HTTP concerns).
4. Add tests to `tests/test_api.py` (and `tests/test_storage.py` for the new
   storage method).
5. If the route should appear in the static site, add it to
  `cli.py::deploy_static`.
6. Update [`docs/API.md`](API.md) with the method, path, auth, rate limit,
   query params, and a response example.
7. Run `python3 -m pytest tests/ -q` and confirm green.

## 8. Adding a new CLI subcommand

1. Add an argparse subparser in `src/agentnews/cli.py`.
2. Implement the command as a function that takes the parsed args and a
   `Config` / `sqlite3.Connection`. Use `require_admin_token(config)` for any
   state-mutating command.
3. Add tests to `tests/test_cli.py`.
4. Update [`docs/API.md`](API.md) §6 and the cheat sheet in
   [`docs/OPERATIONS.md`](OPERATIONS.md) §9.
5. Run `python3 -m pytest tests/ -q` and confirm green.

## 9. Adding a migration

1. Create `src/agentnews/migrations/000N_<name>.sql` with the forward SQL.
2. Register it in `src/agentnews/db.py`'s migration list with its SHA-256
   checksum (the `migrate` command prints the checksum on first apply; or
   compute it with `sha256sum`).
3. Add a test in `tests/test_storage.py` (or `tests/test_cli.py` for the
   `migrate` command) that runs the migration on a fresh DB and asserts the new
   schema.
4. Run `python3 -m agentnews migrate --env-file .env` locally to apply it.
5. Document any new tables/columns in [`docs/ARCHITECTURE.md`](ARCHITECTURE.md)
   §7.

There is no down-migration. Rollback is restore-from-backup.

## 10. Debugging

### `pdb`

```bash
python3 -m pdb -m agentnews serve --env-file .env
# or set breakpoints in source: import pdb; pdb.set_trace()
```

### Inspecting the DB

```bash
sqlite3 ./data/agentnews.db
.tables
.schema packages
SELECT id, status, quality_score FROM packages;
SELECT key_prefix, status, expires_at FROM api_keys;
SELECT endpoint, status_code, COUNT(*) FROM request_log GROUP BY endpoint, status_code;
```

### Reproducing an import failure

```bash
agentnews validate-sneferu-layout /path/to/run --env-file .env
agentnews import-run /path/to/run --dry-run --env-file .env
# --dry-run parses, verifies URLs, runs the admission filter, and prints the
# verdict without writing to the DB
```

### Tracing a request

Every response carries `X-Request-Id` and `X-Response-Time-Ms`. Search
`request_log` for the id:

```bash
sqlite3 ./data/agentnews.db \
  "SELECT endpoint, status_code, response_ms, created_at FROM request_log ORDER BY id DESC LIMIT 10;"
```

## 11. Release checklist

Before tagging a release:

- [ ] `python3 -m pytest tests/ -q` is green.
- [ ] `python3 -m agentnews migrate --env-file .env` applies cleanly on a
      fresh DB.
- [ ] `python3 -m agentnews serve --env-file .env` boots and `/healthz`
      returns 200.
- [ ] A smoke import of a real Sneferu run succeeds (`import-run`).
- [ ] The static site regenerates (`deploy-static`) without errors.
- [ ] [`docs/API.md`](API.md) reflects the current routes and models.
- [ ] [`docs/OPERATIONS.md`](OPERATIONS.md) reflects the current CLI.
- [ ] `setup.py` version is bumped.
- [ ] The changelog (if maintained) is updated.

## 12. Where to look for each concern

| You want to… | Look at |
|---------------|---------|
| Change an API response shape | `src/agentnews/models.py` then the route in `api.py` |
| Add a new operator action | `src/agentnews/cli.py` |
| Change admission rules | `src/agentnews/filter.py` (`admit`) |
| Change how a run is parsed | `src/agentnews/ingest.py` (`parse_run`) |
| Change the DB schema | `src/agentnews/migrations/` + `db.py` |
| Change auth | `src/agentnews/auth.py` |
| Change rate limits | `src/agentnews/ratelimit.py` |
| Change HTML rendering | `src/agentnews/render.py` + `templates/` |
| Change deployment | `scripts/` |
| Understand the whole system | [`docs/ARCHITECTURE.md`](ARCHITECTURE.md) |
