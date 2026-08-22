"""Lightweight stage timing for the /analyze pipeline (Phase 19a, dev-only).

A small context-manager stopwatch that records per-stage wall-clock millisecond
durations. GEE round-trips dominate the pipeline, and this exists purely to show
*where* the time actually goes before any optimization work is attempted.
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Iterator


class Timings(dict):
    """Mutable dict of ``name -> ms`` with a context-manager ``timeit`` helper."""

    def __init__(self) -> None:
        super().__init__()
        self._started = time.perf_counter()

    @contextmanager
    def timeit(self, name: str) -> Iterator[None]:
        t0 = time.perf_counter()
        try:
            yield
        finally:
            self[name] = (time.perf_counter() - t0) * 1000.0

    def elapsed_ms(self, name: str) -> None:
        """Record the duration since the last ``elapsed_ms`` / construction call."""
        now = time.perf_counter()
        self[name] = (now - getattr(self, "_last", self._started)) * 1000.0
        self._last = now

    def total_ms(self) -> float:
        return (time.perf_counter() - self._started) * 1000.0
