"""Hard wall-clock timeout wrapper around GEE calls (Phase 19d).

The GEE Python client has no read timeout of its own, so a stalled server-side
compute (or a rate-limit queue) can hang a ``/analyze`` request indefinitely.
This runs a GEE call on a worker thread with a hard deadline and raises
``GeeTimeoutError`` (mapped to HTTP 504) when the deadline passes. The abandoned
worker thread cannot be killed in CPython and is left to finish or die on its
own, but it never blocks the request again.

This deliberately wraps *calls*, not pipeline logic: the phases are unchanged,
only their wall-clock ceiling is enforced.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from typing import Callable, TypeVar

logger = logging.getLogger("backend.gee_timeout")

T = TypeVar("T")


class GeeTimeoutError(Exception):
    """A GEE round-trip exceeded its hard wall-clock deadline."""


def call_with_timeout(
    fn: Callable[[], T],
    timeout_s: float,
    label: str = "GEE call",
) -> T:
    """Run ``fn`` on a worker thread and enforce a hard ``timeout_s`` deadline.

    Returns ``fn()``'s result on success, re-raises its exception on failure,
    and raises ``GeeTimeoutError`` when the deadline passes first.
    """
    pool = ThreadPoolExecutor(max_workers=1)
    try:
        future = pool.submit(fn)
        try:
            return future.result(timeout=timeout_s)
        except FutureTimeout:
            logger.error("%s exceeded %.0fs hard timeout", label, timeout_s)
            raise GeeTimeoutError(
                f"{label} timed out after {timeout_s:.0f}s — GEE is slow or "
                "unreachable. Try again, or use a cached preset."
            ) from None
        except Exception:
            raise
    finally:
        # Never block on the abandoned worker thread (GEE has no read timeout).
        pool.shutdown(wait=False)
