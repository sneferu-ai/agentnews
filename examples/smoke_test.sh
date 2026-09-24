#!/usr/bin/env bash
#
# smoke_test.sh — end-to-end AgentNews smoke test in a throwaway directory.
#
# Exercises: init, migrate, import-run, sample set, key create, serve, and
# every public + authenticated HTTP endpoint. Uses the run-good fixture and
# a temp .env with insecure mode. Cleans up on exit.
#
# Usage:  bash examples/smoke_test.sh
# Exit:   0 all checks passed, 1 a check failed.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TMP_DIR="$(mktemp -d /tmp/agentnews-smoke-XXXXXX)"
ENV_FILE="$TMP_DIR/.env"
PORT=18799
BASE_URL="http://127.0.0.1:$PORT"
SERVER_PID=""

cleanup() {
    if [ -n "$SERVER_PID" ] && kill -0 "$SERVER_PID" 2>/dev/null; then
        kill "$SERVER_PID" 2>/dev/null || true
        wait "$SERVER_PID" 2>/dev/null || true
    fi
    rm -rf "$TMP_DIR"
}
trap cleanup EXIT

pass() { echo "PASS: $1"; }
fail() { echo "FAIL: $1"; exit 1; }

check_status() {
    local expected="$1" label="$2" url="$3" auth_header="${4:-}"
    local status
    if [ -n "$auth_header" ]; then
        status=$(curl -s -o /dev/null -w "%{http_code}" -H "$auth_header" "$url")
    else
        status=$(curl -s -o /dev/null -w "%{http_code}" "$url")
    fi
    [ "$status" = "$expected" ] && pass "$label -> $status" || fail "$label -> expected $expected, got $status"
}

check_json_field() {
    local label="$1" url="$2" auth_header="${3:-}" field="$4" expected="$5"
    local body actual
    if [ -n "$auth_header" ]; then
        body=$(curl -s -H "$auth_header" "$url")
    else
        body=$(curl -s "$url")
    fi
    actual=$(printf '%s' "$body" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('$field',''))" 2>/dev/null || echo "")
    [ "$actual" = "$expected" ] && pass "$label -> $field=$expected" || fail "$label -> expected $field=$expected, got '$actual'"
}

echo "=== AgentNews smoke test ==="
echo "temp dir: $TMP_DIR"

# 1. Write .env
cat > "$ENV_FILE" <<EOF
PUBLIC_URL=http://127.0.0.1:$PORT
PAYMENT_LINK_URL=https://buy.example.com/agentnews
ADMIN_TOKEN=test-admin-token-for-smoke
AGENTNEWS_DB=$TMP_DIR/agentnews.db
AGENTNEWS_HOST=127.0.0.1
AGENTNEWS_PORT=$PORT
AGENTNEWS_ALLOW_INSECURE=true
SNEFERU_RUNS_DIR=$REPO_ROOT/tests/fixtures
EOF

cd "$REPO_ROOT"
export PYTHONPATH="$REPO_ROOT/src${PYTHONPATH:+:$PYTHONPATH}"

# 2. init + migrate
INIT_OUT=$(python3 -m agentnews init --env-file "$ENV_FILE" 2>&1)
echo "$INIT_OUT" | grep -q "init complete" && pass "init" || fail "init: $INIT_OUT"

MIGRATE_OUT=$(python3 -m agentnews migrate --env-file "$ENV_FILE" 2>&1)
echo "$MIGRATE_OUT" | grep -q "migrations applied" && pass "migrate" || fail "migrate: $MIGRATE_OUT"

# 3. Import the run-good fixture
IMPORT_OUT=$(python3 -m agentnews import-run "$REPO_ROOT/tests/fixtures/run-good" --skip-verify-urls --env-file "$ENV_FILE" 2>&1)
echo "$IMPORT_OUT" | grep -q "imported pid=" && pass "import-run" || fail "import-run: $IMPORT_OUT"
PID=$(echo "$IMPORT_OUT" | grep -o 'pid=pkg-[^ ]*' | cut -d= -f2)
echo "  package id: $PID"

# 4. Mark as sample
SAMPLE_OUT=$(python3 -m agentnews sample set "$PID" --env-file "$ENV_FILE" 2>&1)
echo "$SAMPLE_OUT" | grep -q "as sample" && pass "sample set" || fail "sample set: $SAMPLE_OUT"

# 5. Create API key
KEY_OUT=$(python3 -m agentnews key create --label "smoke test" --env-file "$ENV_FILE" 2>&1)
API_KEY=$(echo "$KEY_OUT" | grep -o 'key=ak_[a-f0-9]*' | cut -d= -f2)
[ -n "$API_KEY" ] && pass "key create (${API_KEY:0:8}...)" || fail "key create: $KEY_OUT"

# 6. Start server
python3 -m agentnews serve --env-file "$ENV_FILE" > "$TMP_DIR/server.log" 2>&1 &
SERVER_PID=$!
sleep 2

# Verify server is up
curl -sf "$BASE_URL/healthz" > /dev/null 2>&1 || fail "server did not start (check $TMP_DIR/server.log)"
pass "server started on port $PORT"

# 7. Endpoint checks
AUTH="X-API-Key: $API_KEY"

check_status "200" "healthz"          "$BASE_URL/healthz"           ""
check_status "200" "openapi.json"      "$BASE_URL/v1/openapi.json"   ""
check_status "200" "catalog"           "$BASE_URL/v1/catalog"        ""
check_status "200" "sample"            "$BASE_URL/v1/sample"         ""
check_status "200" "verify"            "$BASE_URL/v1/packages/$PID/verify" ""
check_status "401" "packages (no key)" "$BASE_URL/v1/packages"       ""
check_status "200" "packages (key)"    "$BASE_URL/v1/packages"       "$AUTH"
check_status "200" "fetch bundle"      "$BASE_URL/v1/packages/$PID"  "$AUTH"
check_status "200" "index (HTML)"      "$BASE_URL/"                  ""
check_status "200" "article (HTML)"    "$BASE_URL/articles/$PID"     ""
check_status "200" "feed (Atom)"       "$BASE_URL/feed.xml"          ""
check_status "404" "article 404"       "$BASE_URL/articles/nonexistent" ""

check_json_field "healthz status"     "$BASE_URL/healthz"           ""  "status"      "ok"
check_json_field "sample id"          "$BASE_URL/v1/sample"         ""  "id"          "$PID"
check_json_field "verify match"       "$BASE_URL/v1/packages/$PID/verify" "" "match"  "True"
check_json_field "fetch question"     "$BASE_URL/v1/packages/$PID"  "$AUTH" "id"      "$PID"

# 8. Security headers
HEADERS=$(curl -s -I "$BASE_URL/" 2>&1)
echo "$HEADERS" | grep -qi "X-Content-Type-Options: nosniff" && pass "security header: nosniff" || fail "missing nosniff"
echo "$HEADERS" | grep -qi "X-Frame-Options: DENY" && pass "security header: frame-deny" || fail "missing frame-deny"
echo "$HEADERS" | grep -qi "script-src 'none'" && pass "security header: CSP script-src none" || fail "missing CSP"

echo ""
echo "ALL CHECKS PASSED"
