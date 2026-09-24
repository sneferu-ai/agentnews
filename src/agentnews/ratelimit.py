"""Rolling-window rate limiting with burst allowance (FR-013, FR-014, §5.2).

Per-key requests are bounded by a rolling 24-hour window plus a small burst
allowance (FR-014). Per-IP requests to the unauthenticated ``/v1/sample``
endpoint are capped at 10/day (FR-022) with a secondary per-``user_agent_hash``
cap of 10/day. Per-IP requests to ``/v1/catalog`` are capped at 60/day
(FR-032) with no secondary UA cap. State is in-memory (deques of timestamps)
and warmed from ``request_log`` on startup.
"""
from __future__ import annotations

import sqlite3
import time
from collections import deque
from typing import Deque, Dict, Optional

WINDOW_S = 86400.0  # 24h


class RateLimiter:
    def __init__(
        self,
        per_day: int,
        burst: int,
        ip_per_day: int = 10,
        catalog_ip_per_day: int = 60,
        ua_per_day: int = 10,
    ) -> None:
        self.per_day = per_day
        self.burst = burst
        self.ip_per_day = ip_per_day
        self.catalog_ip_per_day = catalog_ip_per_day
        self.ua_per_day = ua_per_day
        self._keys: Dict[str, Deque[float]] = {}
        self._ips: Dict[str, Deque[float]] = {}          # sample endpoint
        self._ips_catalog: Dict[str, Deque[float]] = {}   # catalog endpoint
        self._uas: Dict[str, Deque[float]] = {}           # sample UA hash cap

    def _prune(self, dq: Deque[float], now: float) -> None:
        cutoff = now - WINDOW_S
        while dq and dq[0] < cutoff:
            dq.popleft()

    def allow_key(self, key_prefix: str) -> bool:
        now = time.monotonic()
        dq = self._keys.setdefault(key_prefix, deque())
        self._prune(dq, now)
        limit = self.per_day + self.burst
        if len(dq) >= limit:
            return False
        dq.append(now)
        return True

    def allow_ip(self, ip_hash: str) -> bool:
        now = time.monotonic()
        dq = self._ips.setdefault(ip_hash, deque())
        self._prune(dq, now)
        if len(dq) >= self.ip_per_day:
            return False
        dq.append(now)
        return True

    def allow_ip_catalog(self, ip_hash: str) -> bool:
        """FR-032: per-IP cap for /v1/catalog (60/day)."""
        now = time.monotonic()
        dq = self._ips_catalog.setdefault(ip_hash, deque())
        self._prune(dq, now)
        if len(dq) >= self.catalog_ip_per_day:
            return False
        dq.append(now)
        return True

    def allow_ua(self, ua_hash: str) -> bool:
        """FR-022: secondary per-user_agent_hash cap for /v1/sample (10/day)."""
        now = time.monotonic()
        dq = self._uas.setdefault(ua_hash, deque())
        self._prune(dq, now)
        if len(dq) >= self.ua_per_day:
            return False
        dq.append(now)
        return True

    def remaining_key(self, key_prefix: str) -> int:
        """Remaining requests for *key_prefix* in the current window (after pruning)."""
        now = time.monotonic()
        dq = self._keys.get(key_prefix, deque())
        self._prune(dq, now)
        return max(0, self.per_day + self.burst - len(dq))

    def remaining_ip(self, ip_hash: str) -> int:
        """Remaining requests for *ip_hash* in the current window (after pruning)."""
        now = time.monotonic()
        dq = self._ips.get(ip_hash, deque())
        self._prune(dq, now)
        return max(0, self.ip_per_day - len(dq))

    def remaining_ip_catalog(self, ip_hash: str) -> int:
        """FR-032: remaining catalog IP requests."""
        now = time.monotonic()
        dq = self._ips_catalog.get(ip_hash, deque())
        self._prune(dq, now)
        return max(0, self.catalog_ip_per_day - len(dq))

    def is_burst_key(self, key_prefix: str) -> bool:
        """FR-014: True if the last served request was in the burst zone."""
        now = time.monotonic()
        dq = self._keys.get(key_prefix, deque())
        self._prune(dq, now)
        return self.per_day < len(dq) <= self.per_day + self.burst

    def retry_after_key(self, key_prefix: str) -> int:
        """FR-014: seconds until the oldest key timestamp in the window expires."""
        now = time.monotonic()
        dq = self._keys.get(key_prefix, deque())
        self._prune(dq, now)
        if not dq:
            return 0
        return max(1, int(dq[0] + WINDOW_S - now))

    def retry_after_ip(self, ip_hash: str) -> int:
        """FR-022: seconds until the oldest IP timestamp in the window expires."""
        now = time.monotonic()
        dq = self._ips.get(ip_hash, deque())
        self._prune(dq, now)
        if not dq:
            return 0
        return max(1, int(dq[0] + WINDOW_S - now))

    def retry_after_ip_catalog(self, ip_hash: str) -> int:
        """FR-032: seconds until the oldest catalog IP timestamp expires."""
        now = time.monotonic()
        dq = self._ips_catalog.get(ip_hash, deque())
        self._prune(dq, now)
        if not dq:
            return 0
        return max(1, int(dq[0] + WINDOW_S - now))

    def retry_after_ua(self, ua_hash: str) -> int:
        """FR-022: seconds until the oldest UA hash timestamp expires."""
        now = time.monotonic()
        dq = self._uas.get(ua_hash, deque())
        self._prune(dq, now)
        if not dq:
            return 0
        return max(1, int(dq[0] + WINDOW_S - now))

    def warm_from_log(self, conn: sqlite3.Connection) -> None:
        """FR-014/FR-022/FR-032: warm counters from request_log (last 24h) on startup."""
        try:
            rows = conn.execute(
                "SELECT key_prefix, ip_hash, user_agent_hash, endpoint, created_at "
                "FROM request_log WHERE created_at >= datetime('now','-1 day')"
            ).fetchall()
        except sqlite3.OperationalError:
            return
        now = time.monotonic()
        for r in rows:
            ts = _log_to_mono(r["created_at"], now)
            if ts is None:
                continue
            kp = r["key_prefix"]
            iph = r["ip_hash"]
            uah = r["user_agent_hash"]
            endpoint = r["endpoint"] or ""
            if kp:
                self._keys.setdefault(kp, deque()).append(ts)
            if iph:
                if "/v1/catalog" in endpoint:
                    self._ips_catalog.setdefault(iph, deque()).append(ts)
                else:
                    self._ips.setdefault(iph, deque()).append(ts)
            if uah and "/v1/sample" in endpoint:
                self._uas.setdefault(uah, deque()).append(ts)

    def reset(self) -> None:
        self._keys.clear()
        self._ips.clear()
        self._ips_catalog.clear()
        self._uas.clear()


def _log_to_mono(created_at: str, now_mono: float) -> Optional[float]:
    """Convert a sqlite datetime string to a monotonic-ish anchor.

    request_log stores wall-clock ``datetime('now')``; we approximate the
    monotonic offset using the newest row as ~now. Good enough for warm-only.
    """
    try:
        import datetime as _dt

        dt = _dt.datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    # request_log stores wall-clock datetimes without timezone; treat as UTC.
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_dt.timezone.utc)
    # map the row's age in seconds back from now
    age = (_now_wall() - dt).total_seconds()
    if age < 0:
        age = 0
    return now_mono - age


def _now_wall() -> "_dt.datetime":
    import datetime as _dt

    return _dt.datetime.now(_dt.timezone.utc)
