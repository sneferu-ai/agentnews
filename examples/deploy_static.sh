#!/usr/bin/env bash
#
# deploy_static.sh — render the current AgentNews database into a static site.
#
# Exercises: deploy-static CLI. The static output is ready to be served by
# nginx or a CDN alongside the JSON API.
#
# Usage:  bash examples/deploy_static.sh
# Exit:   0 if the static site was produced, 1 otherwise.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="${ENV_FILE:-$REPO_ROOT/.env}"
OUTPUT_DIR="${OUTPUT_DIR:-$REPO_ROOT/static_build}"

cd "$REPO_ROOT"
export PYTHONPATH="$REPO_ROOT/src${PYTHONPATH:+:$PYTHONPATH}"

if [ ! -f "$ENV_FILE" ]; then
    echo "ERROR: $ENV_FILE not found. Copy .env.example to .env and fill it in first."
    exit 1
fi

# Run deploy-static, overriding the output directory for this demo.
python3 -m agentnews deploy-static --output "$OUTPUT_DIR" --env-file "$ENV_FILE"

# Verify the expected output files exist.
for f in index.html access.html 404.html 503.html; do
    if [ ! -f "$OUTPUT_DIR/$f" ]; then
        echo "ERROR: expected output file missing: $OUTPUT_DIR/$f"
        exit 1
    fi
done

if [ ! -d "$OUTPUT_DIR/static" ]; then
    echo "ERROR: expected static asset directory missing: $OUTPUT_DIR/static"
    exit 1
fi

if [ -d "$OUTPUT_DIR/articles" ]; then
    article_count=$(find "$OUTPUT_DIR/articles" -name "*.html" | wc -l | tr -d ' ')
    echo "Articles rendered: $article_count"
else
    echo "NOTE: no articles/ directory — the database has no published packages."
fi

echo "Static site written to $OUTPUT_DIR"
ls -la "$OUTPUT_DIR"
