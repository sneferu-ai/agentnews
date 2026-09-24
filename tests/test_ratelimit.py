"""Tests for the rolling-window rate limiter (FR-013, FR-014, FR-022, FR-032)."""
from agentnews import db, ratelimit


def _make_conn(tmp_path):
    path = str(tmp_path / "rl.db")
    conn = db.connect(path)
    db.run_migrations(conn)
    return conn


def test_key_limit_blocks_after_limit():
    lim = ratelimit.RateLimiter(per_day=2, burst=0)
    assert lim.allow_key("k")
    assert lim.allow_key("k")
    assert not lim.allow_key("k")


def test_key_remaining_and_burst_warning():
    lim = ratelimit.RateLimiter(per_day=2, burst=2)
    assert lim.allow_key("k")
    assert lim.remaining_key("k") == 3
    assert lim.allow_key("k")
    assert lim.is_burst_key("k") is False
    assert lim.allow_key("k")  # first burst request
    assert lim.is_burst_key("k")
    assert lim.allow_key("k")  # second burst request
    assert not lim.allow_key("k")
    assert lim.remaining_key("k") == 0


def test_retry_after_key_positive_after_use():
    lim = ratelimit.RateLimiter(per_day=1, burst=0)
    lim.allow_key("k")
    assert lim.retry_after_key("k") > 0


def test_retry_after_key_zero_when_empty():
    lim = ratelimit.RateLimiter(per_day=1, burst=0)
    assert lim.retry_after_key("k") == 0


def test_ip_and_catalog_counters_are_separate():
    lim = ratelimit.RateLimiter(per_day=10, burst=0, ip_per_day=1, catalog_ip_per_day=5)
    ip = "iph"
    assert lim.allow_ip(ip)
    assert not lim.allow_ip(ip)
    assert lim.allow_ip_catalog(ip)
    assert lim.allow_ip_catalog(ip)
    assert lim.remaining_ip_catalog(ip) >= 3


def test_ua_counter_and_retry_after():
    lim = ratelimit.RateLimiter(per_day=10, burst=0, ua_per_day=1)
    ua = "uah"
    assert lim.allow_ua(ua)
    assert not lim.allow_ua(ua)
    assert lim.retry_after_ua(ua) > 0


def test_warm_from_log_counts_catalog_and_sample(tmp_path):
    conn = _make_conn(tmp_path)
    conn.execute(
        "INSERT INTO request_log "
        "(key_prefix, method, endpoint, status_code, response_time_ms, ip_hash, user_agent_hash, request_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("kp", "GET", "/v1/catalog", 200, 1, "iph", "uah", "r1"),
    )
    conn.execute(
        "INSERT INTO request_log "
        "(key_prefix, method, endpoint, status_code, response_time_ms, ip_hash, user_agent_hash, request_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (None, "GET", "/v1/sample", 200, 1, "iph", "uah", "r2"),
    )
    conn.commit()

    lim = ratelimit.RateLimiter(per_day=10, burst=0, ip_per_day=10, catalog_ip_per_day=10, ua_per_day=10)
    lim.warm_from_log(conn)
    assert lim.remaining_key("kp") == 9
    assert lim.remaining_ip("iph") == 9
    assert lim.remaining_ip_catalog("iph") == 9


def test_reset_clears_all_counters():
    lim = ratelimit.RateLimiter(per_day=10, burst=0, ip_per_day=10, catalog_ip_per_day=10, ua_per_day=10)
    lim.allow_key("k")
    lim.allow_ip("ip")
    lim.allow_ip_catalog("ip")
    lim.allow_ua("ua")
    lim.reset()
    assert lim.remaining_key("k") == 10
    assert lim.remaining_ip("ip") == 10
    assert lim.remaining_ip_catalog("ip") == 10
    assert lim.allow_ua("ua")
