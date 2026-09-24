#!/usr/bin/env python3
"""fetch_catalog.py — fetch the public AgentNews catalog and free sample.

No API key is required. This is the reader-facing surface: it shows what
bundles are published and what the redacted free sample looks like.

Dependencies: Python 3.9+ standard library only.
Usage:
    python3 examples/fetch_catalog.py --base-url http://127.0.0.1:8000
"""

import argparse
import json
import sys
import urllib.error
import urllib.request


def get(base_url: str, path: str) -> dict:
    """GET a public JSON endpoint. Returns parsed JSON or exits on failure."""
    url = base_url.rstrip("/") + path
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        print(f"HTTP {exc.code} from {url}: {body}", file=sys.stderr)
        sys.exit(1)
    except urllib.error.URLError as exc:
        print(f"Connection error to {url}: {exc.reason}", file=sys.stderr)
        sys.exit(1)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fetch the public AgentNews catalog and free sample."
    )
    parser.add_argument(
        "--base-url",
        required=True,
        help="AgentNews base URL, e.g. http://127.0.0.1:8000",
    )
    args = parser.parse_args()

    catalog = get(args.base_url, "/v1/catalog")
    packages = catalog.get("packages", [])
    print(f"Catalog: {len(packages)} package(s) published")
    for pkg in packages:
        print(
            f"  {pkg['id']}: {pkg.get('question', 'untitled')} "
            f"(score: {pkg.get('score', 'n/a')})"
        )

    print()
    sample = get(args.base_url, "/v1/sample")
    print("Free sample:")
    print(f"  id:       {sample.get('id', 'n/a')}")
    print(f"  question: {sample.get('question', 'n/a')}")
    print(f"  score:    {sample.get('score', 'n/a')}")
    print(f"  summary:  {sample.get('summary', 'n/a')}")
    print(f"  citations: {len(sample.get('citations', []))}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
