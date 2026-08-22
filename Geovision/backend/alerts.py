"""Severity-triggered email alerts.

When an analysis comes back severity == "severe", notify the person
responsible for that area so they can act on it without watching the
dashboard. Runs as a FastAPI BackgroundTask so it never adds latency to
/analyze, and a misconfigured/offline mail server never breaks a result.
"""

from __future__ import annotations

import json
import logging
import os
import smtplib
import threading
import time
from email.mime.text import MIMEText

from backend.models.schemas import AnalyzeResponse

logger = logging.getLogger("hackpreneur")

ALERT_SEVERITY = "severe"
ALERT_COOLDOWN_SECONDS = int(os.environ.get("ALERT_COOLDOWN_SECONDS", "1800"))

_last_sent: dict[str, float] = {}
_lock = threading.Lock()


def _recipients() -> dict[str, str]:
    raw = os.environ.get("ALERT_RECIPIENTS_JSON", "")
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        logger.error("ALERT_RECIPIENTS_JSON is not valid JSON; ignoring.")
        return {}


def _resolve_recipient(preset_id: str | None) -> str | None:
    if preset_id:
        email = _recipients().get(preset_id)
        if email:
            return email
    return os.environ.get("ALERT_DEFAULT_EMAIL") or None


def _on_cooldown(key: str) -> bool:
    with _lock:
        last = _last_sent.get(key)
        now = time.time()
        if last is not None and (now - last) < ALERT_COOLDOWN_SECONDS:
            return True
        _last_sent[key] = now
        return False


def _send_email(to_addr: str, subject: str, body: str) -> None:
    host = os.environ.get("SMTP_HOST")
    port = int(os.environ.get("SMTP_PORT", "587"))
    user = os.environ.get("SMTP_USER")
    password = os.environ.get("SMTP_PASSWORD")
    from_addr = os.environ.get("ALERT_FROM_EMAIL", user or "alerts@geovision.local")

    if not host or not user or not password:
        logger.warning(
            "Email alert skipped — SMTP_HOST/SMTP_USER/SMTP_PASSWORD not fully "
            "configured; would have sent to %s: %s", to_addr, subject,
        )
        return

    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = to_addr

    with smtplib.SMTP(host, port, timeout=10) as server:
        server.starttls()
        server.login(user, password)
        server.sendmail(from_addr, [to_addr], msg.as_string())


def maybe_send_alert(response: AnalyzeResponse, preset_id: str | None) -> None:
    if response.severity != ALERT_SEVERITY:
        return

    recipient = _resolve_recipient(preset_id)
    if not recipient:
        logger.info(
            "Severe change detected (%s) but no alert recipient configured "
            "for preset=%s — set ALERT_RECIPIENTS_JSON or ALERT_DEFAULT_EMAIL.",
            response.location_name, preset_id,
        )
        return

    cooldown_key = preset_id or response.location_name or "unknown"
    if _on_cooldown(cooldown_key):
        logger.info("Alert for %s suppressed (cooldown active).", cooldown_key)
        return

    subject = f"[GeoVision] Severe {response.mode.upper()} change detected — {response.location_name or 'unnamed area'}"
    body = (
        f"{response.alert_message}\n\n"
        f"Location: {response.location_name or 'unknown'}\n"
        f"Affected: {response.affected_ha} ha ({response.affected_pct}% of monitored area)\n"
        f"Window: {response.before_date} -> {response.after_date}\n\n"
        "Recommended actions:\n"
        + "\n".join(f"  - {a}" for a in response.recommended_actions)
        + "\n\nThis is an automated alert from GeoVision. No reply necessary."
    )

    try:
        _send_email(recipient, subject, body)
        logger.info("Severity alert emailed to %s for %s", recipient, cooldown_key)
    except Exception as exc:
        logger.error("Failed to send severity alert to %s: %s", recipient, exc)
