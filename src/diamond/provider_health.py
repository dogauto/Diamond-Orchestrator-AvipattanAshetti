"""Provider health tracking with quota awareness and quarantine."""

import json
import time
from dataclasses import dataclass, field
from typing import Any, Mapping

from .diagnostics import ProbeResult, RateLimitInfo
from .domain import Provider
from .errors import ValidationError
from .store import EventStore


@dataclass(frozen=True)
class ProviderHealth:
    """Current health state for a provider."""
    provider: Provider
    last_probe_ts: float
    rate_limit: RateLimitInfo | None
    consecutive_failures: int
    last_error: str | None
    is_quarantined: bool
    quarantine_until: float | None
    last_successful_dispatch_ts: float | None

    def is_healthy(self, min_remaining: int = 1, now: float | None = None) -> bool:
        """Check if provider is healthy enough for dispatch."""
        if now is None:
            now = time.monotonic()
        if self.is_quarantined:
            if self.quarantine_until is not None and now >= self.quarantine_until:
                return False  # quarantine expired but not yet released
            return False
        if self.rate_limit is not None and self.rate_limit.remaining is not None:
            if self.rate_limit.remaining < min_remaining:
                return False
        return True

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider.value,
            "last_probe_ts": self.last_probe_ts,
            "rate_limit": self.rate_limit.to_dict() if self.rate_limit else None,
            "consecutive_failures": self.consecutive_failures,
            "last_error": self.last_error,
            "is_quarantined": self.is_quarantined,
            "quarantine_until": self.quarantine_until,
            "last_successful_dispatch_ts": self.last_successful_dispatch_ts,
        }

    @classmethod
    def default(cls, provider: Provider) -> "ProviderHealth":
        return cls(
            provider=provider,
            last_probe_ts=0.0,
            rate_limit=None,
            consecutive_failures=0,
            last_error=None,
            is_quarantined=False,
            quarantine_until=None,
            last_successful_dispatch_ts=None,
        )


class ProviderHealthStore:
    """Persistent provider health state backed by EventStore database."""

    def __init__(self, ledger: EventStore) -> None:
        self.ledger = ledger

    def _ensure_table(self) -> None:
        with self.ledger._connection(write=True) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS provider_health (
                    provider TEXT PRIMARY KEY,
                    last_probe_ts REAL NOT NULL,
                    rate_limit_json TEXT,
                    consecutive_failures INT DEFAULT 0,
                    last_error TEXT,
                    is_quarantined INT DEFAULT 0,
                    quarantine_until REAL,
                    last_successful_dispatch_ts REAL
                )
            """)

    def upsert_probe(self, provider: Provider, result: ProbeResult) -> None:
        """Update health from a probe result."""
        self._ensure_table()
        now = time.monotonic()
        rl_json = json.dumps(result.rate_limit.to_dict()) if result.rate_limit else None
        with self.ledger._connection(write=True) as conn:
            conn.execute("""
                INSERT INTO provider_health (provider, last_probe_ts, rate_limit_json,
                    consecutive_failures, last_error, is_quarantined, quarantine_until,
                    last_successful_dispatch_ts)
                VALUES (?, ?, ?,
                    COALESCE((SELECT consecutive_failures FROM provider_health WHERE provider=?), 0),
                    COALESCE((SELECT last_error FROM provider_health WHERE provider=?), NULL),
                    COALESCE((SELECT is_quarantined FROM provider_health WHERE provider=?), 0),
                    COALESCE((SELECT quarantine_until FROM provider_health WHERE provider=?), NULL),
                    COALESCE((SELECT last_successful_dispatch_ts FROM provider_health WHERE provider=?), NULL))
                ON CONFLICT(provider) DO UPDATE SET
                    last_probe_ts=excluded.last_probe_ts,
                    rate_limit_json=excluded.rate_limit_json
            """, (provider.value, now, rl_json, provider.value, provider.value, provider.value, provider.value, provider.value))

    def record_dispatch_start(self, provider: Provider) -> None:
        """Record that a dispatch started (for tracking)."""
        self._ensure_table()
        with self.ledger._connection(write=True) as conn:
            conn.execute("""
                INSERT INTO provider_health (provider, last_probe_ts, consecutive_failures, is_quarantined)
                VALUES (?, 0, 0, 0)
                ON CONFLICT(provider) DO NOTHING
            """, (provider.value,))

    def record_dispatch_result(self, provider: Provider, success: bool, error: str | None) -> None:
        """Update health after a dispatch attempt."""
        self._ensure_table()
        now = time.monotonic()
        with self.ledger._connection(write=True) as conn:
            if success:
                conn.execute("""
                    UPDATE provider_health SET
                        consecutive_failures = 0,
                        last_error = NULL,
                        is_quarantined = 0,
                        quarantine_until = NULL,
                        last_successful_dispatch_ts = ?
                    WHERE provider = ?
                """, (now, provider.value))
            else:
                # Increment failures, check for quarantine
                row = conn.execute("""
                    SELECT consecutive_failures FROM provider_health WHERE provider = ?
                """, (provider.value,)).fetchone()
                failures = (row[0] if row else 0) + 1
                quarantine = 1 if failures >= 3 else 0
                quarantine_until = now + 300.0 if quarantine else None  # 5 min default
                conn.execute("""
                    UPDATE provider_health SET
                        consecutive_failures = ?,
                        last_error = ?,
                        is_quarantined = ?,
                        quarantine_until = ?
                    WHERE provider = ?
                """, (failures, error, quarantine, quarantine_until, provider.value))

    def get_health(self, provider: Provider) -> ProviderHealth:
        """Get current health state for a provider."""
        self._ensure_table()
        with self.ledger._connection(write=False) as conn:
            row = conn.execute("""
                SELECT provider, last_probe_ts, rate_limit_json, consecutive_failures,
                       last_error, is_quarantined, quarantine_until, last_successful_dispatch_ts
                FROM provider_health WHERE provider = ?
            """, (provider.value,)).fetchone()
        if row is None:
            return ProviderHealth.default(provider)
        rl = None
        if row[2]:
            try:
                rl_data = json.loads(row[2])
                rl = RateLimitInfo(**rl_data)
            except Exception:
                pass
        return ProviderHealth(
            provider=Provider(row[0]),
            last_probe_ts=row[1],
            rate_limit=rl,
            consecutive_failures=row[3],
            last_error=row[4],
            is_quarantined=bool(row[5]),
            quarantine_until=row[6],
            last_successful_dispatch_ts=row[7],
        )

    def list_healthy(self, min_remaining: int = 1, now: float | None = None) -> list[ProviderHealth]:
        """List all providers that meet health criteria."""
        self._ensure_table()
        with self.ledger._connection(write=False) as conn:
            rows = conn.execute("""
                SELECT provider, last_probe_ts, rate_limit_json, consecutive_failures,
                       last_error, is_quarantined, quarantine_until, last_successful_dispatch_ts
                FROM provider_health
            """).fetchall()
        healthy = []
        for row in rows:
            rl = None
            if row[2]:
                try:
                    rl_data = json.loads(row[2])
                    rl = RateLimitInfo(**rl_data)
                except Exception:
                    pass
            health = ProviderHealth(
                provider=Provider(row[0]),
                last_probe_ts=row[1],
                rate_limit=rl,
                consecutive_failures=row[3],
                last_error=row[4],
                is_quarantined=bool(row[5]),
                quarantine_until=row[6],
                last_successful_dispatch_ts=row[7],
            )
            if health.is_healthy(min_remaining, now):
                healthy.append(health)
        return healthy

    def quarantine(self, provider: Provider, duration_seconds: float) -> None:
        """Manually quarantine a provider."""
        self._ensure_table()
        now = time.monotonic()
        with self.ledger._connection(write=True) as conn:
            conn.execute("""
                UPDATE provider_health SET
                    is_quarantined = 1,
                    quarantine_until = ?
                WHERE provider = ?
            """, (now + duration_seconds, provider.value))

    def release_quarantine(self, provider: Provider) -> None:
        """Release a provider from quarantine."""
        self._ensure_table()
        with self.ledger._connection(write=True) as conn:
            conn.execute("""
                UPDATE provider_health SET
                    is_quarantined = 0,
                    quarantine_until = NULL,
                    consecutive_failures = 0
                WHERE provider = ?
            """, (provider.value,))

    def release_expired_quarantines(self, now: float | None = None) -> int:
        """Release all expired quarantines. Returns count released."""
        if now is None:
            now = time.monotonic()
        self._ensure_table()
        with self.ledger._connection(write=True) as conn:
            cursor = conn.execute("""
                UPDATE provider_health SET
                    is_quarantined = 0,
                    quarantine_until = NULL,
                    consecutive_failures = 0
                WHERE is_quarantined = 1 AND quarantine_until IS NOT NULL AND quarantine_until <= ?
            """, (now,))
            return cursor.rowcount