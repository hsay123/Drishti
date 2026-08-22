"""Google Earth Engine client initialization and connectivity checks."""

from __future__ import annotations

import os
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout

import ee

GEE_PROJECT_ID = "project-326ab593-31e9-43ed-8cd"

_initialized = False


class GeeUnavailableError(RuntimeError):
    """Raised when GEE cannot be reached or the project is not authorized."""


def _restore_credentials_from_env() -> None:
    """Write personal OAuth credentials from an env var to the on-disk path
    the ``ee`` client reads by default.

    This is the deploy-friendly alternative to a GEE service-account key
    (which our org policy blocks us from creating). ``earthengine
    authenticate`` normally writes this exact JSON to
    ``~/.config/earthengine/credentials`` on a machine where you've logged
    in interactively. To deploy: cat that local file's contents, paste them
    verbatim into a Render env var named ``EE_CREDENTIALS_JSON``, and this
    function rehydrates the file at container startup so ``ee.Initialize``
    picks it up exactly as it would locally. No service account involved.
    """
    raw = os.environ.get("EE_CREDENTIALS_JSON")
    if not raw:
        return
    cred_path = Path.home() / ".config" / "earthengine" / "credentials"
    cred_path.parent.mkdir(parents=True, exist_ok=True)
    cred_path.write_text(raw)


def initialize(project: str = GEE_PROJECT_ID) -> None:
    """Initialize the Earth Engine session exactly once per process."""
    global _initialized
    if _initialized:
        return
    _restore_credentials_from_env()
    try:
        ee.Initialize(project=project)
    except Exception as exc:  # ee.EEException / auth errors
        raise GeeUnavailableError(
            f"Failed to initialize Google Earth Engine with project '{project}': {exc}"
        ) from exc
    _initialized = True


def check_connectivity(project: str = GEE_PROJECT_ID, timeout_s: float = 8.0) -> bool:
    """Return True if GEE is reachable and responds to a trivial request.

    The probe runs on a worker thread with a hard ``timeout_s`` so a GEE
    network outage can never wedge the server (the ee client has no read
    timeout of its own and would otherwise hang startup /health for minutes).
    """
    initialize(project)

    def _probe() -> bool:
        # Cheap round-trip that proves auth + network + project access.
        return ee.Number(1).add(1).getInfo() == 2

    pool = ThreadPoolExecutor(max_workers=1)
    try:
        future = pool.submit(_probe)
        try:
            return future.result(timeout=timeout_s)
        except FutureTimeout:
            return False
        except Exception:
            return False
    finally:
        # Never block on the abandoned probe thread (GEE has no read timeout).
        pool.shutdown(wait=False)