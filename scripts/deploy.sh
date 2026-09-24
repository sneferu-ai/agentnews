#!/bin/bash
set -euo pipefail

# Production deploy helper for AgentNews.
# Builds the wheel, installs it into the configured venv, runs migrations,
# re-renders the static site, and restarts the systemd unit.

INSTALL_DIR="${AGENTNEWS_INSTALL:-/opt/agentnews}"
VENV="${AGENTNEWS_VENV:-$INSTALL_DIR/.venv}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

cd "$REPO_ROOT"
python3 -m build

WHEEL="$(ls -t "$REPO_ROOT"/dist/agentnews-*.whl | head -1)"
"$VENV/bin/pip" install --force-reinstall "$WHEEL"

"$VENV/bin/agentnews" migrate
"$VENV/bin/agentnews" deploy-static --output "$INSTALL_DIR/static_build"

if systemctl is-active --quiet agentnews 2>/dev/null; then
    systemctl restart agentnews
fi

echo "Deploy complete: $WHEEL"
