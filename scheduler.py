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
from email_notifier import send_outage_email

log = logging.getLogger(__name__)

MONITOR_INTERVAL_MINUTES = int(os.environ.get("MONITOR_INTERVAL_MINUTES", "5"))
TARGET_URL = os.environ.get("TARGET_URL", "https://auraxl.com")

_scheduler = BackgroundScheduler(daemon=True, timezone="UTC")
_scheduler_started = False


def _scheduled_check():
    """Run a monitoring check, persist the result, and dispatch alerts if down."""
    target_url = db.get_setting("target_url", os.environ.get("TARGET_URL", "https://auraxl.com"))
    email_enabled = db.get_setting("email_alerts_enabled", "true").lower() == "true"
    alert_email = db.get_setting("alert_email", os.environ.get("ALERT_EMAIL", "31pranav104@gmail.com"))

    try:
        result = monitor.run_check(target_url=target_url)
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
        
        # Dispatch alert email if website is down and email alerts are enabled
        if not result["is_up"] and email_enabled:
            send_outage_email(
                target_url=result["target_url"],
                error_type=result["error_type"],
                error_message=result["error_message"],
                response_time_ms=result["response_time_ms"],
                recipient=alert_email
            )
    except Exception:
        log.exception("Error in scheduled check")


def update_schedule(interval_minutes: int):
    """Dynamically reschedule the recurring monitoring check."""
    try:
        _scheduler.reschedule_job(
            "auraxl_check",
            trigger=IntervalTrigger(minutes=interval_minutes, timezone="UTC")
        )
        log.info("[Scheduler] Rescheduled check interval to every %d minutes", interval_minutes)
    except Exception as e:
        log.error("[Scheduler] Error rescheduling: %s", e)


def start_scheduler():
    """
    Start the background scheduler (idempotent — safe to call multiple times).
    Also runs an immediate check on startup so the dashboard is not empty.
    """
    global _scheduler_started
    if _scheduler_started:
        return

    _scheduler_started = True
    interval = int(db.get_setting("monitor_interval_minutes", os.environ.get("MONITOR_INTERVAL_MINUTES", "5")))

    # Register the recurring job
    _scheduler.add_job(
        func=_scheduled_check,
        trigger=IntervalTrigger(minutes=interval, timezone="UTC"),
        id="auraxl_check",
        name="AuraXL site check",
        replace_existing=True,
        max_instances=1,          # prevent overlapping runs
        coalesce=True,            # if delayed, run once not multiple times
    )

    _scheduler.start()
    log.info(
        "[Scheduler] Started — checking target every %d minutes", interval
    )

    # Run an immediate check so data is available right away
    log.info("[Scheduler] Running initial check…")
    _scheduled_check()


def trigger_now():
    """Run a check immediately (called by POST /api/check-now)."""
    _scheduled_check()
    target_url = db.get_setting("target_url", os.environ.get("TARGET_URL", "https://auraxl.com"))
    return db.get_latest_check(target_url)
