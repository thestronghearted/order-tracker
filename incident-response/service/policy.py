"""Decides whether an incoming alert should trigger a new investigation.

This is the "authorize" step: real-world incident responders don't act on every
signal blindly - they check it's a legitimate, actionable, not-already-being-worked
alert first. Here that means: only "firing" alerts start an investigation, and only
one investigation per alert name runs at a time.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field


@dataclass
class InFlightTracker:
    """Tracks which alert names currently have an investigation running.

    Grafana retries webhook deliveries and can re-fire while a fix is still in
    progress; without this, a second headless agent could start editing the
    same repository concurrently with the first.
    """

    _active: set[str] = field(default_factory=set)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def try_start(self, key: str) -> bool:
        with self._lock:
            if key in self._active:
                return False
            self._active.add(key)
            return True

    def finish(self, key: str) -> None:
        with self._lock:
            self._active.discard(key)

    def is_active(self, key: str) -> bool:
        with self._lock:
            return key in self._active
