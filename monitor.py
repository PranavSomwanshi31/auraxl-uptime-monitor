"""
monitor.py — HTTP checker for AuraXL Uptime Monitor.

Sends a GET request to the target URL, measures response time,
and classifies the result as: up / degraded / down.

Availability rules:
  up       — HTTP 200–399 AND response_time_ms < DEGRADED_RESPONSE_TIME_MS
  degraded — HTTP 200–399 AND response_time_ms >= DEGRADED_RESPONSE_TIME_MS
  down     — HTTP 400+, connection error, timeout, DNS failure
"""

import os
import time
import logging
from datetime import datetime, timezone

import httpx

log = logging.getLogger(__name__)

# Configuration (read once at module load; can be overridden at call time)
TARGET_URL = os.environ.get("TARGET_URL", "https://auraxl.com")
REQUEST_TIMEOUT_MS = int(os.environ.get("REQUEST_TIMEOUT_MS", "10000"))
DEGRADED_RESPONSE_TIME_MS = int(os.environ.get("DEGRADED_RESPONSE_TIME_MS", "3000"))

_USER_AGENT = (
    "AuraXL-Uptime-Monitor/1.0 "
    "(+https://github.com/PranavSomwanshi31/auraxl-uptime-monitor; "
    "site-availability-monitoring)"
)

_HEADERS = {
    "User-Agent": _USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}


def run_check(
    target_url: str = None,
    timeout_ms: int = None,
    degraded_ms: int = None,
) -> dict:
    """
    Perform a single HTTP GET check against `target_url`.

    Returns a dict with keys:
      target_url, checked_at, http_status, response_time_ms,
      is_up, status, error_type, error_message
    """
    url = target_url or TARGET_URL
    timeout_secs = (timeout_ms or REQUEST_TIMEOUT_MS) / 1000.0
    degraded_threshold = degraded_ms or DEGRADED_RESPONSE_TIME_MS

    checked_at = datetime.now(timezone.utc)
    http_status = None
    response_time_ms = None
    is_up = False
    status = "down"
    error_type = None
    error_message = None

    start = time.monotonic()
    try:
        with httpx.Client(
            follow_redirects=True,
            timeout=httpx.Timeout(connect=5.0, read=timeout_secs, write=5.0, pool=5.0),
            headers=_HEADERS,
            verify=True,
        ) as client:
            response = client.get(url)

        elapsed_ms = int((time.monotonic() - start) * 1000)
        http_status = response.status_code
        response_time_ms = elapsed_ms

        if 200 <= http_status < 400:
            is_up = True
            if elapsed_ms >= degraded_threshold:
                status = "degraded"
            else:
                status = "up"
        else:
            is_up = False
            status = "down"
            error_type = "http_error"
            error_message = f"HTTP {http_status}"

    except httpx.ConnectTimeout:
        response_time_ms = int((time.monotonic() - start) * 1000)
        error_type = "connect_timeout"
        error_message = "Connection timed out"
    except httpx.ReadTimeout:
        response_time_ms = int((time.monotonic() - start) * 1000)
        error_type = "read_timeout"
        error_message = "Read timed out"
    except httpx.ConnectError as exc:
        response_time_ms = int((time.monotonic() - start) * 1000)
        msg = str(exc)
        if "getaddrinfo" in msg.lower() or "name or service not known" in msg.lower():
            error_type = "dns_failure"
            error_message = "DNS resolution failed"
        else:
            error_type = "connection_error"
            error_message = f"Connection error: {msg[:200]}"
    except httpx.TooManyRedirects:
        response_time_ms = int((time.monotonic() - start) * 1000)
        error_type = "too_many_redirects"
        error_message = "Too many redirects"
    except httpx.HTTPError as exc:
        response_time_ms = int((time.monotonic() - start) * 1000)
        error_type = "http_error"
        error_message = f"HTTP error: {str(exc)[:200]}"
    except Exception as exc:
        response_time_ms = int((time.monotonic() - start) * 1000)
        error_type = "unexpected_error"
        error_message = f"Unexpected error: {type(exc).__name__}"
        log.exception("Unexpected error during check of %s", url)

    result = {
        "target_url": url,
        "checked_at": checked_at,
        "http_status": http_status,
        "response_time_ms": response_time_ms,
        "is_up": is_up,
        "status": status,
        "error_type": error_type,
        "error_message": error_message,
    }

    _log_result(result)
    return result


def _log_result(r: dict):
    tag = {"up": "[UP]", "degraded": "[DEGRADED]", "down": "[DOWN]"}.get(r["status"], "[?]")
    if r["is_up"]:
        log.info(
            "%s %s - HTTP %s - %dms",
            tag, r["target_url"], r["http_status"], r["response_time_ms"]
        )
    else:
        log.warning(
            "%s %s - %s: %s",
            tag, r["target_url"], r["error_type"], r["error_message"]
        )
