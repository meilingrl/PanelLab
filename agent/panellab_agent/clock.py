"""Injectable clock so scheduling logic can be tested without sleeping."""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone


class Clock:
    def now(self) -> datetime:
        """Current time as a timezone-aware UTC datetime."""
        return datetime.now(timezone.utc)

    def monotonic(self) -> float:
        return time.monotonic()


class FakeClock(Clock):
    def __init__(self, start: datetime) -> None:
        self._now = start
        self._mono = 0.0

    def now(self) -> datetime:
        return self._now

    def monotonic(self) -> float:
        return self._mono

    def advance(self, seconds: float) -> None:
        self._now = self._now + timedelta(seconds=seconds)
        self._mono += seconds
