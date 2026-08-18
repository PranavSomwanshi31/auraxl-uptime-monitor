"""
scheduler.py — APScheduler wrapper for AuraXL Uptime Monitor.

Runs run_check() every MONITOR_INTERVAL_MINUTES minutes in a background thread.
The scheduler is started once when the Flask app boots and
stores results directly into the database.
"""

import os
import logging
from datetime import datetime, timezone

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

import monitor
import db

log = logging.getLogger(__name__)

MONITOR_INTERVAL_MINUTES = int(os.environ.get("MONITOR_INTERVAL_MINUTES", "5"))
TARGET_URL = os.environ.get("TARGET_URL", "https://auraxl.com")

_scheduler = BackgroundScheduler(daemon=True, timezone="UTC")
_scheduler_started = False


def _scheduled_check():
    """Run a monitoring check and persist the result."""
    try:
        result = monitor.run_check(target_url=TARGET_URL)
        db.insert_check(
            target_url=result["target_url"],
            checked_at=result["checked_at"],
            http_status=result["http_status"],
            response_time_ms=result["response_time_ms"],
            is_up=result["is_up"],
            status=result["status"],
            error_type=result["error_type"],
            error_message=result["error_message"],
        )
    except Exception:
        log.exception("Error in scheduled check")


def start_scheduler():
    """
    Start the background scheduler (idempotent — safe to call multiple times).
    Also runs an immediate check on startup so the dashboard is not empty.
    """
    global _scheduler_started
    if _scheduler_started:
        return

    _scheduler_started = True

    # Register the recurring job
    _scheduler.add_job(
        func=_scheduled_check,
        trigger=IntervalTrigger(minutes=MONITOR_INTERVAL_MINUTES, timezone="UTC"),
        id="auraxl_check",
        name="AuraXL site check",
        replace_existing=True,
        max_instances=1,          # prevent overlapping runs
        coalesce=True,            # if delayed, run once not multiple times
    )

    _scheduler.start()
    log.info(
        "[Scheduler] Started — checking %s every %d minutes",
        TARGET_URL, MONITOR_INTERVAL_MINUTES
    )

    # Run an immediate check so data is available right away
    log.info("[Scheduler] Running initial check…")
    _scheduled_check()


def trigger_now():
    """Run a check immediately (called by POST /api/check-now)."""
    _scheduled_check()
    return db.get_latest_check(TARGET_URL)
