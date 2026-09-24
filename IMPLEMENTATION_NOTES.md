# Implementation Notes — Repair Round 1

## What the failure was

The Sneferu runtime-test phase (`orchestrator/core/pipeline_runner.py`
~line 9915) failed with:

> runtime_test_enabled=True but no test_plan.md found at
> `<code_run>/final/test_plan.md`, under `<code_run>/tree/`, or declared
> by the built tree's packaging.json (packaging.json declares no
> browser_smoke_plan).

The runtime-test phase looks for the test plan in three sources, in order:
1. `<code_run>/final/test_plan.md` — the code phase's frozen artifact.
2. `<code_run>/tree/*/test_plan.md` — per-node tree output.
3. The file named in `packaging.json`'s `browser_smoke_plan` field, resolved
   as a contained relative path inside the built tree (the product's own
   declaration).

None of the three existed. The product shipped `packaging.json` with a
`runtime_test_command` but no `browser_smoke_plan`, and no `test_plan.md`
anywhere in the tree. Per the failure message, this is a real failure of
the test-planning step, not a benign opt-out — the artifact remained
unqualified while construction continued to other evidence phases.

## What I changed

Four files, all at the worktree root or in helper directories. No Python
source files under `src/` were modified — the product's scope and
architecture are preserved exactly.

### 1. `test_plan.md` (new file, 76 lines)

A runtime smoke-test plan for the AgentNews HTTP API service, written as a
numbered list of 20 discrete checks. The Sneferu `parse_test_plan` parser
(`orchestrator/core/runtime_test.py:1306`) extracts each numbered item into
a structured `TestPlanItem` with a stable ID (TP-001 through TP-020); item
1 (GET /healthz) is the primary boot-proof journey.

The plan is now **executable against the runtime command** rather than just
descriptive:
- It names the deterministic runtime-test API key
  (`ak_00000000000000000000000000000001`).
- It names the deterministic seeded package ID (`pkg-runtime-test-0001`),
  so exact paths like `/v1/packages/pkg-runtime-test-0001` can be used.
- Preconditions state that the runtime start command
  (`python3 scripts/runtime_serve.py {port}`) performs the seeding.

The plan covers:
- Primary journey: health check (boot proof)
- Public surface (no auth): index, catalog, sample, feed, access, openapi
- Paid subscriber gate: 401 without key, 200 with key, full bundle fetch
- Content integrity: verify endpoint (post-import tamper detection)
- HTML article rendering: certificate, reuse panel, verify link
- Failure paths: HTML 404 for browser routes, JSON 404 for API routes,
  429 rate limiting with Retry-After
- Security headers on every response (CSP script-src 'none', no
  unsafe-inline)
- No-JavaScript invariant (FR-035)
- ETag / conditional GET (304)
- Static assets (CSS, reuse license)

Each item names the exact endpoint, the expected status code, and the
assertion that distinguishes a pass from a skeleton — consistent with the
existing static test suite (`tests/test_api.py`) and the
`tests/e2e_journey.py` CLI journey.

### 2. `scripts/runtime_serve.py` (new file)

The runtime-test start command now delegates to a dedicated seed-and-serve
script instead of launching uvicorn directly. This is necessary because the
runtime-test harness does not seed the database itself; a smoke plan that
expects a paid bundle and a subscriber key would otherwise fail against an
empty store.

The script:
- Accepts a single `<port>` argument.
- Sets a self-contained runtime-test environment (insecure localhost URL,
  payment link, admin token, log salt, DB path, rate limits, insecure mode).
- Removes any stale SQLite DB/WAL/SHM files and creates a fresh database.
- Runs migrations.
- Imports the checked-in `tests/fixtures/run-good` fixture.
- Marks the imported package as the free sample.
- Publishes the package so it appears in public endpoints.
- Inserts the deterministic API key
  `ak_00000000000000000000000000000001`.
- Uses a deterministic package ID `pkg-runtime-test-0001` so the static
  test plan can reference exact paths.
- Starts `uvicorn` with the same keep-alive and graceful-shutdown values
  the original command used.

### 3. `packaging.json` (edited)

```json
{
  "runtime_test_command": "python3 scripts/runtime_serve.py {port}",
  "runtime_test_mode": "http",
  "browser_smoke_plan": "test_plan.md",
  "health_endpoint": "/healthz",
  "install_command": "python3 -m pip install -e ."
}
```

- `runtime_test_command` — now points at the seed-and-serve script. The
  `{port}` placeholder remains so the runtime harness can substitute the
  allocated port.
- `browser_smoke_plan: "test_plan.md"` — the load-bearing field the
  runtime-test phase's third source (`resolve_declared_browser_smoke_plan`,
  `orchestrator/core/build_readiness.py:783`) reads. It must be a contained
  relative path (no `..`, no absolute, no symlink component) resolving to a
  regular file inside the built tree. `test_plan.md` at the root satisfies
  this.
- `runtime_test_mode: "http"` — declares the mode explicitly. The artifact
  type is `local_api` whose policy requires `http` mode.
- `health_endpoint: "/healthz"` — declares the readiness probe the runtime
  harness polls before running the test items.
- `install_command` — explicit install step for the Python package so the
  runtime harness does not have to guess.

### 4. `tests/test_runtime.py` (new file, 5 tests)

A focused test suite that exercises the same path the runtime-test phase
uses:
- `test_packaging_declares_runtime_contract` — verifies `packaging.json`
  declares the required fields and that `test_plan.md` exists and is a
  regular file.
- `test_runtime_serve_seeds_health_and_catalog` — starts the runtime serve
  script on a throwaway DB and verifies `/healthz` and `/v1/catalog`.
- `test_runtime_serve_paid_api_gated_and_readable` — verifies the 401 gate
  and the authenticated package/list endpoints.
- `test_runtime_serve_verify_and_article` — verifies content-hash
  verification and the human-readable article page.
- `test_runtime_serve_rate_limit` — verifies the per-key 429 response with
  `Retry-After`.

## Verification

1. **Runtime-test phase resolution (simulated).** Ran
   `resolve_declared_browser_smoke_plan` against the worktree root:
   resolves to `<tree>/test_plan.md`, source = "packaging.json". The plan
   parses into 20 `TestPlanItem` objects; TP-001 is primary.

2. **Runtime serve script (manual).** Spawned the script on a throwaway
   port + DB and confirmed:
   - `/healthz` returns 200 with `db_ok=true` and `packages_count=1`.
   - `/v1/catalog` lists `pkg-runtime-test-0001`.
   - `/v1/packages` without key returns 401.
   - `/v1/packages` with the deterministic key returns 200.
   - `/v1/packages/pkg-runtime-test-0001` returns the full bundle with
     5 citations and a `sha256:` content hash.
   - `/v1/packages/pkg-runtime-test-0001/verify` returns `match=true`.
   - `/articles/pkg-runtime-test-0001` renders the certificate page and
     contains no `<script` tag.
   - Repeated authenticated requests trigger 429 with `Retry-After`.

3. **Test suite.** `python3 -m pytest tests/ -q` → 172 passed (167 existing
   + 5 new). No source files under `src/` were touched, so the existing
   tests are unaffected by the product-code changes.

## What I did NOT do

- Did not touch any Python source file under `src/`. The product's approved
  scope and architecture are preserved.
- Did not disable `runtime_test_enabled`. The failure message offered two
  repair paths: (a) set `runtime_test_enabled=False`, or (b) fix the code
  phase so it produces a `test_plan.md`. Path (b) is the correct repair —
  the product is a `local_api` whose policy mandates runtime testing, and
  disabling it would convert a real qualification gate into a silent skip
  (the exact anti-pattern FIX-174 was written to prevent).

---

# Repair Round 2 — install command exit 1

## What the failure was

The Sneferu runtime-test phase reported:

> Final product runtime did not qualify: declared install command exited
> non-zero (1)
> Evidence: declared install command exited non-zero (1)

The `packaging.json` declares `"install_command": "python3 -m pip install -e ."`.
Running that command in the runtime environment (Python 3.9.6, pip 21.2.4,
setuptools 58.0.4, read-only system site-packages) produced:

```
ERROR: File "setup.py" or "setup.cfg" not found. Directory cannot be
installed in editable mode: ...
(A "pyproject.toml" file was found, but editable mode currently requires
a setuptools-based build.)
```

pip 21.2.4 predates PEP 660 (editable installs from a `pyproject.toml`-only
project). It requires a `setup.py` or `setup.cfg` for the legacy editable
path. The product shipped `pyproject.toml` only — no `setup.py`.

After adding `setup.py`, a second failure surfaced: pip's PEP 517 build
isolation installs a newer setuptools (≥62) into the isolated build env, and
that newer setuptools's `develop` command treats the empty `--prefix=` that
pip 21.2.4 passes as "prefix is set", overriding `--user` and targeting the
read-only system site-packages — a permission-denied exit 1.

## What I changed

Three files at the worktree root (no Python source under `src/` was touched):

1. **`setup.py` (new)** — sole packaging configuration. Carries the full
   metadata that was in `pyproject.toml`'s `[project]` table explicitly
   (setuptools 58 cannot read PEP 621). Includes a defensive `develop`
   subclass that pre-sets `install_dir` to the user site under `--user` so
   any future environment running a newer setuptools cannot redirect the
   editable install into the read-only system tree.

2. **`pyproject.toml` (removed)** — its `[build-system]` declaring
   `requires = ["setuptools>=68"]` was incompatible with the runtime env
   (setuptools 58), and its mere presence triggered PEP 517 build isolation
   which installed a newer setuptools whose `develop` command broke
   `--user --prefix=`. Removing it makes pip use the legacy `setup.py` path
   with the system setuptools, which honours `--user` correctly.

3. **`pytest.ini` (new)** — preserves the `testpaths = tests` config that
   was in `pyproject.toml`'s `[tool.pytest.ini_options]`.

Four doc files updated to reference `setup.py` instead of the removed
`pyproject.toml`: `docs/SECURITY.md`, `docs/OPERATIONS.md`,
`docs/DEVELOPMENT.md` (3 references: repo layout, dev extras, release
checklist).

## Verification

1. **Install command (the failing gate):**
   `python3 -m pip install -e .` → exit 0, "Successfully installed
   agentnews-0.1.0", `import agentnews` works, `agentnews` console script
   installed and responds to `--help`.

2. **Test suite:** `python3 -m pytest -q` → 172 passed, 75 warnings in 7s.
   No regressions — the test suite uses `conftest.py`'s `sys.path.insert`
   for imports, not the install, so it is unaffected by the packaging
   change. `pytest.ini` preserves the `testpaths` config.

3. **`pip install -e . --no-build-isolation`** also exits 0 (confirmed the
   legacy path works independently of build isolation).

## What I did NOT do

- Did not touch any Python source file under `src/`. The product's approved
  scope, architecture, API, CLI, templates, migrations, and tests are
  unchanged.
- Did not modify `packaging.json` — the declared `install_command` is
  correct; the failure was that the project lacked the build file the
  command requires.
- Did not pin `setuptools` to a specific version in a `[build-system]`
  table — that would have required either keeping `pyproject.toml` (which
  triggers build isolation) or adding a `setup.cfg` with the same issue.
  The legacy `setup.py`-only path is the cleanest compatible solution.
