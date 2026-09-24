"""Test configuration: path setup + shared fixtures."""
import os
import sys
import tempfile
from pathlib import Path

# Make `agentnews` importable from src/ (no install needed in test env).
SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

# Set required env vars BEFORE importing agentnews modules.
os.environ.setdefault("PUBLIC_URL", "https://news.example.com")
os.environ.setdefault("PAYMENT_LINK_URL", "https://buy.example.com/agentnews")
os.environ.setdefault("ADMIN_TOKEN", "test-admin-token")
os.environ.setdefault("SNEFERU_RUNS_DIR", "/tmp/agentnews-sneferu-runs")
os.environ.setdefault("LOG_SALT", "test-salt-fixed")
os.environ.setdefault("AGENTNEWS_ALLOW_INSECURE", "false")
os.environ.setdefault("PUBLISHABLE_SCORE_THRESHOLD", "60")
os.environ.setdefault("CITATION_MINIMUM", "3")
os.environ.setdefault("EXCERPT_LIMIT_WORDS", "300")
os.environ.setdefault("URL_VERIFY_TOTAL_TIMEOUT", "5")
os.environ.setdefault("RATE_LIMIT_PER_DAY", "100")
os.environ.setdefault("BURST_ALLOWANCE", "20")
os.environ.setdefault("KEY_EXPIRY_DAYS", "90")
os.environ.setdefault("FOUNDING_PRICE_USD", "100")

import sqlite3  # noqa: E402

import pytest  # noqa: E402

from agentnews import db as agentnews_db  # noqa: E402
from agentnews.config import load_config  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture()
def tmp_db_path(tmp_path):
    return str(tmp_path / "agentnews.db")


@pytest.fixture()
def conn(tmp_db_path):
    c = agentnews_db.connect(tmp_db_path)
    agentnews_db.run_migrations(c)
    yield c
    c.close()


@pytest.fixture()
def config(tmp_db_path):
    os.environ["AGENTNEWS_DB"] = tmp_db_path
    return load_config()


@pytest.fixture()
def good_run_dir():
    return FIXTURES / "run-good"


@pytest.fixture()
def bad_run_dir():
    return FIXTURES / "run-bad"


@pytest.fixture()
def minimal_run_dir():
    return FIXTURES / "run-minimal"


@pytest.fixture()
def client(config, conn):
    """A Starlette TestClient wired to a test app with the temp DB."""
    from starlette.testclient import TestClient

    from agentnews.api import create_app

    app = create_app(config=config, conn=conn, run_lifespan=False)
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def strict_client(tmp_db_path, monkeypatch):
    """A client with a burst of 1 and per-day limit of 1 to test 429 responses."""
    from starlette.testclient import TestClient
    from agentnews.api import create_app
    from agentnews.config import load_config

    monkeypatch.setenv("AGENTNEWS_DB", tmp_db_path)
    monkeypatch.setenv("RATE_LIMIT_PER_DAY", "1")
    monkeypatch.setenv("BURST_ALLOWANCE", "0")
    cfg = load_config()
    conn = agentnews_db.connect(tmp_db_path)
    agentnews_db.run_migrations(conn)
    app = create_app(config=cfg, conn=conn, run_lifespan=False)
    with TestClient(app) as c:
        yield c
    conn.close()


@pytest.fixture()
def admin_headers():
    return {"X-Admin-Token": "test-admin-token"}
