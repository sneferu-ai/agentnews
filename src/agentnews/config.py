"""Configuration loading from environment variables and .env files.

All runtime configuration is via environment variables (spec §7). The ``.env``
file is parsed with a small stdlib parser (python-dotenv is not a hard
dependency so the test environment needs no extra packages).
"""
from __future__ import annotations

import os
import re
import secrets
import sys
from dataclasses import dataclass
from typing import Optional


REQUIRED_ENV = ("PUBLIC_URL", "PAYMENT_LINK_URL", "ADMIN_TOKEN")

_DEFAULT_RUN_TYPE_REGEX = r"^\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}Z-(\w+)-[a-f0-9]+$"


def _truthy(value: Optional[str]) -> bool:
    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "yes", "on"}


def parse_env_file(path: str) -> dict:
    """Parse a simple ``.env`` file into a dict. Never raises on missing file."""
    out: dict = {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for raw in fh:
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("export "):
                    line = line[len("export "):].lstrip()
                if "=" not in line:
                    continue
                key, _, val = line.partition("=")
                key = key.strip()
                val = val.strip()
                if (len(val) >= 2 and val[0] in "\"'" and val[-1] == val[0]):
                    val = val[1:-1]
                out[key] = val
    except FileNotFoundError:
        pass
    return out


def load_env_file(path: str = ".env") -> None:
    """Load values from ``.env`` into ``os.environ`` without overriding set vars."""
    for key, val in parse_env_file(path).items():
        if key not in os.environ:
            os.environ[key] = val


def normalize_public_url(url: str) -> str:
    """Remove a trailing slash from PUBLIC_URL (FR-041)."""
    return url.rstrip("/")


def get_env(name: str, default: Optional[str] = None) -> Optional[str]:
    return os.environ.get(name, default)


@dataclass
class Config:
    public_url: str
    payment_link_url: str
    admin_token: str
    sneferu_runs_dir: Optional[str]
    db_path: str
    host: str
    port: int
    publishable_score_threshold: float
    rate_limit_per_day: int
    burst_allowance: int
    excerpt_limit_words: int
    citation_minimum: int
    url_verify_total_timeout: int
    key_expiry_days: int
    staging_delay_minutes: int
    static_output_dir: str
    founding_price_usd: int
    trust_proxy_headers: bool
    allow_insecure: bool
    run_type_regex: str
    log_salt: str

    @property
    def run_type_pattern(self) -> "re.Pattern[str]":
        return re.compile(self.run_type_regex)


def load_config(env_path: str = ".env") -> Config:
    """Load .env then read all env vars into a Config. Does not validate."""
    load_env_file(env_path)
    public_url = normalize_public_url(os.environ.get("PUBLIC_URL", ""))
    return Config(
        public_url=public_url,
        payment_link_url=os.environ.get("PAYMENT_LINK_URL", ""),
        admin_token=os.environ.get("ADMIN_TOKEN", ""),
        sneferu_runs_dir=os.environ.get("SNEFERU_RUNS_DIR") or None,
        db_path=os.environ.get("AGENTNEWS_DB", "./data/agentnews.db"),
        host=os.environ.get("AGENTNEWS_HOST", "127.0.0.1"),
        port=int(os.environ.get("AGENTNEWS_PORT", "8000")),
        publishable_score_threshold=float(
            os.environ.get("PUBLISHABLE_SCORE_THRESHOLD", "60")
        ),
        rate_limit_per_day=int(os.environ.get("RATE_LIMIT_PER_DAY", "100")),
        burst_allowance=int(os.environ.get("BURST_ALLOWANCE", "20")),
        excerpt_limit_words=int(os.environ.get("EXCERPT_LIMIT_WORDS", "300")),
        citation_minimum=int(os.environ.get("CITATION_MINIMUM", "3")),
        url_verify_total_timeout=int(os.environ.get("URL_VERIFY_TOTAL_TIMEOUT", "60")),
        key_expiry_days=int(os.environ.get("KEY_EXPIRY_DAYS", "90")),
        staging_delay_minutes=int(os.environ.get("STAGING_DELAY_MINUTES", "0")),
        static_output_dir=os.environ.get("STATIC_OUTPUT_DIR", "./static_build"),
        founding_price_usd=int(os.environ.get("FOUNDING_PRICE_USD", "100")),
        trust_proxy_headers=_truthy(os.environ.get("TRUST_PROXY_HEADERS", "false")),
        allow_insecure=_truthy(os.environ.get("AGENTNEWS_ALLOW_INSECURE", "false")),
        run_type_regex=os.environ.get("RUN_TYPE_REGEX", _DEFAULT_RUN_TYPE_REGEX),
        log_salt=os.environ.get("LOG_SALT", ""),
    )


class EnvValidationError(Exception):
    def __init__(self, errors: list):
        self.errors = errors
        super().__init__("; ".join(errors))


def validate_required_env(config: Config) -> None:
    """FR-041: validate PUBLIC_URL, PAYMENT_LINK_URL, ADMIN_TOKEN set + HTTPS scheme.

    Only the three vars listed in FR-041 are checked here — this function backs
    ``init``, ``serve``, and ``deploy-static``. ``SNEFERU_RUNS_DIR`` is required
    only for ``watch``/``scan`` (FR-029/FR-030) and is validated at the command
    level, NOT here.

    Raises EnvValidationError with ``ERROR:MISSING_ENV:<var>`` and
    ``ERROR:INSECURE_PUBLIC_URL:<value>`` style messages. When
    ``AGENTNEWS_ALLOW_INSECURE=true`` and ``PUBLIC_URL`` is not HTTPS, prints a
    ``WARNING:INSECURE_PUBLIC_URL:<value>`` message to stderr and continues.
    """
    errors: list = []
    if not config.public_url:
        errors.append("ERROR:MISSING_ENV:PUBLIC_URL")
    if not config.payment_link_url:
        errors.append("ERROR:MISSING_ENV:PAYMENT_LINK_URL")
    if not config.admin_token:
        errors.append("ERROR:MISSING_ENV:ADMIN_TOKEN")
    if config.public_url and not config.public_url.startswith("https://"):
        if config.allow_insecure:
            print(
                f"WARNING:INSECURE_PUBLIC_URL:{config.public_url}",
                file=sys.stderr,
            )
        else:
            errors.append(f"ERROR:INSECURE_PUBLIC_URL:{config.public_url}")
    if errors:
        raise EnvValidationError(errors)


def ensure_log_salt(env_path: str = ".env") -> str:
    """FR-041: generate a random LOG_SALT into .env if absent. Returns the salt."""
    existing = parse_env_file(env_path)
    if existing.get("LOG_SALT"):
        return existing["LOG_SALT"]
    salt = secrets.token_hex(16)
    _append_env_line(env_path, f"LOG_SALT={salt}")
    if "LOG_SALT" not in os.environ:
        os.environ["LOG_SALT"] = salt
    return salt


def _append_env_line(path: str, line: str) -> None:
    needs_newline = False
    try:
        with open(path, "r", encoding="utf-8") as fh:
            content = fh.read()
        if content and not content.endswith("\n"):
            needs_newline = True
    except FileNotFoundError:
        pass
    with open(path, "a", encoding="utf-8") as fh:
        if needs_newline:
            fh.write("\n")
        fh.write(line + "\n")


def require_admin_token(config: Config) -> None:
    """FR-027/§5.4: protected CLI commands require ADMIN_TOKEN."""
    if not config.admin_token:
        raise EnvValidationError(["ERROR:ADMIN_TOKEN_REQUIRED"])
