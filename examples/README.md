# examples/ — AgentNews usage examples

Four self-contained examples that prove the product works end to end. They
are ordered from the broadest operator smoke test to the smallest public-API
call. Every example uses only the Python standard library or common shell
tools, so a writing agent can copy the patterns without adding dependencies.

## smoke_test.sh

A shell script that exercises the full operator + subscriber flow against a
temporary database:

1. Writes a throwaway `.env` (insecure mode, temp DB).
2. Runs `init`, `migrate`.
3. Imports the `tests/fixtures/run-good` fixture run with `--skip-verify-urls`.
4. Marks the imported bundle as the free sample.
5. Creates a subscriber API key.
6. Starts the ASGI server in the background.
7. Curls every public and authenticated endpoint and checks the HTTP status.
8. Stops the server and cleans up the temp directory.

**Run it:**

```bash
bash examples/smoke_test.sh
```

**Expected output:** a series of `PASS` lines for health, catalog, sample,
packages (401 without key, 200 with key), fetch, verify, openapi, index,
article, feed — followed by `ALL CHECKS PASSED`.

This is the same surface the 167-test pytest suite covers, but as a single
shell script an operator can read top to bottom without knowing pytest.

## fetch_bundle.py

A Python script that shows how a writing agent would authenticate and pull a
full research bundle over the paid API. It takes a base URL, an API key, and
an optional package ID (defaults to the first listed package), fetches the
full bundle, and prints the question, score, citation count, verification
states, and the cross-vendor agreement certificate.

**Run it (against a running server):**

```bash
python3 examples/fetch_bundle.py \
    --base-url http://127.0.0.1:8000 \
    --api-key ak_024d9ce6cf23af8caadf1f9352cda6f3
```

Or with a specific package ID:

```bash
python3 examples/fetch_bundle.py \
    --base-url http://127.0.0.1:8000 \
    --api-key ak_024d9ce6cf23af8caadf1f9352cda6f3 \
    --package-id pkg-20260817-d641f7
```

**Dependencies:** Python 3.9+ standard library only (`urllib.request`,
`json`, `argparse`). No third-party packages — a writing agent can embed this
pattern without installing anything beyond the stdlib.

## fetch_catalog.py

A Python script that shows the public (no-key) reader surface: it lists the
published catalog and fetches the redacted free sample. Use this to verify the
newsroom has content before you buy an API key.

**Run it (against a running server):**

```bash
python3 examples/fetch_catalog.py \
    --base-url http://127.0.0.1:8000
```

**Expected output:** a short list of published packages followed by the free
sample's id, question, score, and citation count.

## deploy_static.sh

A shell script that renders the current database into a static site. It runs
`agentnews deploy-static` and then checks that the expected pages
(`index.html`, `access.html`, `404.html`, `503.html`) and the `static/`
asset directory are written. It also reports how many article pages were
rendered; if the database has no published packages, `articles/` will be
empty and the script prints a note rather than failing.

**Run it (with an initialized `.env`):**

```bash
bash examples/deploy_static.sh
```

The default output directory is `./static_build`. Override it with:

```bash
OUTPUT_DIR=/var/www/agentnews bash examples/deploy_static.sh
```
