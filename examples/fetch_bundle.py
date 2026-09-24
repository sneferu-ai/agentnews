#!/usr/bin/env python3
"""
fetch_bundle.py — example writing-agent client for the AgentNews paid API.

Authenticates with an API key, lists published bundles, fetches one full
bundle, and prints a human-readable summary including the question, score,
citation verification states, and the cross-vendor agreement certificate.

Dependencies: Python 3.9+ standard library only.
Usage:
    python3 examples/fetch_bundle.py --base-url http://127.0.0.1:8000 --api-key ak_...
    python3 examples/fetch_bundle.py --base-url http://127.0.0.1:8000 --api-key ak_... --package-id pkg-20260817-xxxxxx
"""

import argparse
import json
import sys
import urllib.error
import urllib.request


def api_get(base_url: str, path: str, api_key: str) -> dict:
    """GET a JSON endpoint with X-API-Key auth. Returns parsed JSON."""
    url = base_url.rstrip("/") + path
    req = urllib.request.Request(url, headers={
        "X-API-Key": api_key,
        "Accept": "application/json",
    })
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


def public_get(base_url: str, path: str) -> dict:
    """GET a public JSON endpoint (no auth). Returns parsed JSON."""
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


def verify_state_label(code: int) -> str:
    labels = {1: "resolved", -1: "restricted", -2: "failed", 0: "unchecked"}
    return labels.get(code, f"unknown({code})")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fetch an AgentNews research bundle (example writing-agent client)."
    )
    parser.add_argument("--base-url", required=True, help="AgentNews base URL, e.g. http://127.0.0.1:8000")
    parser.add_argument("--api-key", required=True, help="Subscriber API key (ak_...)")
    parser.add_argument("--package-id", default=None, help="Package ID to fetch; defaults to the first listed package.")
    args = parser.parse_args()

    # If no package ID given, list packages and pick the first one.
    pid = args.package_id
    if pid is None:
        print(f"Listing packages at {args.base_url}/v1/packages ...")
        listing = api_get(args.base_url, "/v1/packages?limit=1", args.api_key)
        packages = listing.get("packages", [])
        if not packages:
            print("No published packages found.", file=sys.stderr)
            return 1
        pid = packages[0]["id"]
        print(f"  found {listing.get('count', len(packages))} package(s); fetching: {pid}")

    # Fetch the full bundle.
    print(f"\nFetching bundle {pid} ...")
    bundle = api_get(args.base_url, f"/v1/packages/{pid}", args.api_key)

    # Print a human-readable summary.
    print(f"\n{'='*60}")
    print(f"Package:   {bundle['id']}")
    print(f"Run ID:    {bundle.get('run_id', 'n/a')}")
    print(f"Question:  {bundle['question']}")
    print(f"Score:     {bundle.get('score', 'n/a')}")
    print(f"Published: {bundle.get('published_at', 'n/a')}")
    if bundle.get("latency_disclosure"):
        print(f"Latency:   {bundle['latency_disclosure']}")

    # Signature / agreement certificate.
    sig = bundle.get("signature") or {}
    print(f"\n--- Cross-vendor agreement certificate ---")
    print(f"  Cross-vendor: {sig.get('cross_vendor', False)}")
    print(f"  Degraded:     {sig.get('degraded', False)}")
    print(f"  Model families: {', '.join(sig.get('model_families', []))}")
    print(f"  Convergence score: {sig.get('convergence_score', 'n/a')}")
    print(f"  Started:    {sig.get('started_at', 'n/a')}")
    print(f"  Converged:  {sig.get('converged_at', 'n/a')}")

    # Citations.
    citations = bundle.get("citations") or []
    print(f"\n--- Citations ({len(citations)} total) ---")
    for c in citations:
        state = verify_state_label(c.get("verified", 0))
        print(f"  [{c['ordinal']}] {state:>10}  {c.get('title', 'untitled')}")
        if c.get("url"):
            print(f"       url: {c['url']}")

    # Reuse terms.
    terms = bundle.get("reuse_terms") or {}
    print(f"\n--- Reuse terms ---")
    print(f"  License:            {terms.get('license', 'n/a')}")
    print(f"  Attribution req'd:  {terms.get('attribution_required', 'n/a')}")
    print(f"  May republish:      {terms.get('may_republish', 'n/a')}")
    print(f"  Excerpt word limit: {terms.get('max_excerpt_words', 'n/a')}")

    # Content hash + independent tamper check.
    print(f"\n--- Tamper check ---")
    print(f"  Stored hash:     {bundle.get('content_hash', 'n/a')}")
    verify = public_get(args.base_url, f"/v1/packages/{pid}/verify")
    recomputed = verify.get("content_hash_recomputed", "n/a")
    match = verify.get("match", False)
    print(f"  Recomputed hash: {recomputed}")
    print(f"  Match:           {match}")

    # Summary excerpt.
    if bundle.get("summary"):
        print(f"\n--- Summary ---")
        print(f"  {bundle['summary']}")

    # Findings preview (first 500 chars).
    findings = bundle.get("findings") or ""
    if findings:
        print(f"\n--- Findings (first 500 chars) ---")
        print(f"  {findings[:500]}{'...' if len(findings) > 500 else ''}")

    print(f"\n{'='*60}")
    print(f"Bundle {pid} fetched successfully.")
    print(f"A writing agent would now rewrite the findings in its own voice,")
    print(f"cite the {len(citations)} references, and quote the agreement certificate.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
