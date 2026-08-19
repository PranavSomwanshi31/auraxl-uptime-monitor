"""
app.py — Flask application for AuraXL Uptime Monitor.

Routes:
  GET  /                   → dashboard (static HTML)
  GET  /health             → service health (monitoring app)
  GET  /api/status         → latest check result
  GET  /api/checks         → paginated check history
  GET  /api/stats          → uptime / response-time statistics
  POST /api/check-now      → trigger immediate check (token protected)
"""

import os
import logging
import sys
from datetime import datetime, timezone
from functools import wraps

from dotenv import load_dotenv
load_dotenv()

# ── Logging ────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    stream=sys.stdout,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

from flask import Flask, jsonify, request, send_from_directory, abort
from flask_cors import CORS

import db
import scheduler

# ── App setup ──────────────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")

app = Flask(__name__, static_folder=STATIC_DIR)
CORS(app)

TARGET_URL = os.environ.get("TARGET_URL", "https://auraxl.com")
MANUAL_CHECK_TOKEN = os.environ.get("MANUAL_CHECK_TOKEN", "")
MAX_HISTORY_LIMIT = 200  # hard cap on ?limit parameter

# ── Initialise DB and scheduler on startup ─────────────────────────────────
with app.app_context():
    db.init_db()
    scheduler.start_scheduler()


# ── Helpers ────────────────────────────────────────────────────────────────
def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _serialise(row: dict) -> dict:
    """Convert non-JSON-serialisable types in a DB row."""
    if row is None:
        return None
    out = {}
    for k, v in row.items():
        if isinstance(v, datetime):
            out[k] = v.isoformat()
        elif isinstance(v, (bool, int, float, str)) or v is None:
            out[k] = v
        else:
            out[k] = str(v)
    return out


def _require_token(f):
    """Decorator: require Authorization: Bearer <MANUAL_CHECK_TOKEN>."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not MANUAL_CHECK_TOKEN:
            return jsonify({"error": "MANUAL_CHECK_TOKEN not configured on server"}), 503
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            abort(401)
        token = auth[7:].strip()
        if token != MANUAL_CHECK_TOKEN:
            abort(403)
        return f(*args, **kwargs)
    return decorated


# ── Routes ─────────────────────────────────────────────────────────────────

@app.route("/")
def dashboard():
    """Serve the single-page dashboard."""
    return send_from_directory(STATIC_DIR, "dashboard.html")


@app.route("/manifest.json")
def serve_manifest():
    return send_from_directory(STATIC_DIR, "manifest.json", mimetype="application/manifest+json")


@app.route("/sw.js")
def serve_sw():
    return send_from_directory(STATIC_DIR, "sw.js", mimetype="application/javascript")


@app.route("/static/<path:path>")
def serve_static(path):
    return send_from_directory(STATIC_DIR, path)


@app.route("/health")
def health():
    """
    Health check for the monitoring SERVICE ITSELF (not AuraXL).
    Used by Render to determine if this application is alive.
    """
    db_ok = True
    try:
        with db.get_cursor() as cur:
            cur.execute("SELECT 1")
    except Exception as exc:
        db_ok = False
        log.error("DB health check failed: %s", exc)

    return jsonify({
        "status": "healthy" if db_ok else "degraded",
        "service": "auraxl-uptime-monitor",
        "timestamp": _now_iso(),
        "database": "connected" if db_ok else "unavailable",
    }), 200


@app.route("/api/status")
def api_status():
    """Return the latest monitoring check result."""
    target_url = db.get_setting("target_url", TARGET_URL)
    row = db.get_latest_check(target_url) or db.get_latest_check()
    if row is None:
        return jsonify({
            "success": True,
            "data": None,
            "message": "No checks performed yet",
        })
    return jsonify({"success": True, "data": _serialise(row)})


@app.route("/api/settings", methods=["GET"])
def get_settings():
    """Get current monitoring & notification settings."""
    settings = db.get_all_settings()
    return jsonify({"success": True, "data": settings})


@app.route("/api/settings", methods=["POST"])
def update_settings():
    """Save monitoring & notification settings."""
    data = request.get_json() or {}
    
    if "target_url" in data and data["target_url"].strip():
        db.set_setting("target_url", data["target_url"].strip())
        
    if "alert_email" in data:
        db.set_setting("alert_email", data["alert_email"].strip())
        
    if "monitor_interval_minutes" in data:
        try:
            interval = max(1, int(data["monitor_interval_minutes"]))
            db.set_setting("monitor_interval_minutes", str(interval))
            scheduler.update_schedule(interval)
        except ValueError:
            pass
            
    if "email_alerts_enabled" in data:
        db.set_setting("email_alerts_enabled", str(data["email_alerts_enabled"]).lower())
        
    if "push_alerts_enabled" in data:
        db.set_setting("push_alerts_enabled", str(data["push_alerts_enabled"]).lower())

    if "smtp_server" in data:
        db.set_setting("smtp_server", data["smtp_server"].strip())
    if "smtp_port" in data:
        db.set_setting("smtp_port", str(data["smtp_port"]).strip())
    if "smtp_user" in data:
        db.set_setting("smtp_user", data["smtp_user"].strip())
    if "smtp_password" in data and data["smtp_password"].strip():
        db.set_setting("smtp_password", data["smtp_password"].strip())
        
    return jsonify({
        "success": True, 
        "message": "Settings saved successfully!",
        "data": db.get_all_settings()
    })


@app.route("/api/settings/test-email", methods=["POST"])
def test_email_alert():
    """Send a test email alert. Recipient must be explicitly provided — no hardcoded fallback."""
    from email_notifier import send_outage_email, parse_recipients
    data = request.get_json() or {}
    recipient = data.get("email", "").strip()
    target_url = db.get_setting("target_url", "https://auraxl.com")

    if not recipient:
        return jsonify({
            "success": False,
            "message": "Recipient email address is required. Please enter a valid email in the Settings tab."
        }), 400

    validated = parse_recipients(recipient)
    if not validated:
        return jsonify({
            "success": False,
            "message": f"Invalid email format: '{recipient}'. Please enter a properly formatted email address."
        }), 400

    log.info("[API] Dispatching test email to: %s", recipient)

    success, msg = send_outage_email(
        target_url=target_url,
        error_type="TEST VERIFICATION ALERT",
        error_message="This is an automated verification confirming AuraXL 24/7 Uptime Guardian email alerts are active and working.",
        response_time_ms=120,
        recipient=recipient
    )

    return jsonify({
        "success": success,
        "message": msg,
        "recipient": recipient
    })


@app.route("/api/settings/smtp-health", methods=["GET"])
def smtp_health_check():
    """Run a deep SMTP network diagnostic: DNS → TCP ports → SSL → Auth. Safe: never logs passwords."""
    from email_notifier import diagnose_smtp_connection
    log.info("[API] Running SMTP health diagnostic...")
    diag = diagnose_smtp_connection()
    return jsonify({"success": True, "diagnostic": diag})



@app.route("/api/vapid-public-key")
def vapid_public_key():
    """Return the server VAPID public key for browser subscription."""
    from push_notifier import get_vapid_public_key
    key = get_vapid_public_key()
    return jsonify({"publicKey": key})


@app.route("/api/push/subscribe", methods=["POST"])
def push_subscribe():
    """Save a browser Web Push subscription from the frontend."""
    data = request.get_json() or {}
    endpoint = data.get("endpoint", "").strip()
    keys = data.get("keys", {})
    p256dh = keys.get("p256dh", "").strip()
    auth = keys.get("auth", "").strip()
    if not endpoint or not p256dh or not auth:
        return jsonify({"success": False, "error": "Missing subscription fields"}), 400
    db.save_push_subscription(endpoint, p256dh, auth)
    log.info("[Push] Saved push subscription: %s", endpoint[:60])
    return jsonify({"success": True, "message": "Push subscription registered."})


@app.route("/api/push/unsubscribe", methods=["POST"])
def push_unsubscribe():
    """Remove a browser Web Push subscription."""
    data = request.get_json() or {}
    endpoint = data.get("endpoint", "").strip()
    if endpoint:
        db.delete_push_subscription(endpoint)
    return jsonify({"success": True})


@app.route("/api/push/test", methods=["POST"])
def push_test():
    """Send a test push notification to all registered devices."""
    from push_notifier import send_push_to_all
    subs = db.get_all_push_subscriptions()
    if not subs:
        return jsonify({"success": False, "message": "No push subscriptions registered yet. Please enable push notifications in Settings first."})
    success, expired = send_push_to_all(
        subs,
        title="✅ AuraXL Monitor — Test Alert",
        body="Push notifications are working! You will be alerted when the site goes down, even with the app closed."
    )
    for ep in expired:
        db.delete_push_subscription(ep)
    if success > 0:
        return jsonify({"success": True, "message": f"Test push sent to {success} device(s) successfully!"})
    else:
        return jsonify({"success": False, "message": "Push delivery failed. Ensure VAPID_PUBLIC_KEY and VAPID_PRIVATE_KEY are set in Render environment variables."})



@app.route("/api/agent/recovery-diagnosis", methods=["GET", "POST"])
def api_agent_recovery():
    """Trigger the Agentic Auto-Recovery & Deep Diagnostic Agent."""
    import ai_agent
    target_url = request.args.get("target_url") or db.get_setting("target_url", TARGET_URL)
    result = ai_agent.run_deep_recovery_diagnosis(target_url)
    return jsonify({"success": True, "data": result})


@app.route("/api/agent/trends")
def api_agent_trends():
    """Trigger the Agentic Trend & Latency Anomaly Forecast Agent."""
    import ai_agent
    result = ai_agent.generate_trend_analytics()
    return jsonify({"success": True, "data": result})


@app.route("/api/agent/executive-report")
def api_agent_report():
    """Trigger the Agentic Executive SLA Audit & Report Agent."""
    import ai_agent
    result = ai_agent.generate_executive_report()
    return jsonify({"success": True, "data": result})


@app.route("/api/timeline")
def api_timeline():
    """Return structured visual timeline blocks (uptime, downtime, degraded, maintenance)."""
    hours = int(request.args.get("hours", 24))
    limit = min(int(request.args.get("limit", 100)), 150)
    checks = db.get_checks(limit=limit, page=1)
    
    # Reverse so oldest is first
    chronological = list(reversed(checks)) if checks else []
    
    timeline_blocks = []
    for c in chronological:
        st = c.get("status", "unknown")
        # Check if error indicates scheduled maintenance or downtime
        err_msg = c.get("error_message") or ""
        if "maintenance" in err_msg.lower():
            segment_type = "maintenance"
        elif st == "up":
            segment_type = "uptime"
        elif st == "degraded":
            segment_type = "degraded"
        else:
            segment_type = "downtime"
            
        timeline_blocks.append({
            "id": c.get("id"),
            "timestamp": c.get("checked_at"),
            "status": st,
            "type": segment_type,
            "http_status": c.get("http_status"),
            "response_time_ms": c.get("response_time_ms"),
            "error_message": c.get("error_message")
        })
        
    return jsonify({
        "success": True,
        "hours": hours,
        "total_segments": len(timeline_blocks),
        "data": timeline_blocks
    })



@app.route("/api/checks")
def api_checks():
    """
    Return paginated monitoring history.
    Query params: limit (int), page (int), status (str), from (ISO ts), to (ISO ts)
    """
    try:
        limit = min(int(request.args.get("limit", 50)), MAX_HISTORY_LIMIT)
        page = max(int(request.args.get("page", 1)), 1)
    except ValueError:
        return jsonify({"error": "limit and page must be integers"}), 400

    status_filter = request.args.get("status")
    if status_filter and status_filter not in ("up", "degraded", "down"):
        return jsonify({"error": "status must be up, degraded, or down"}), 400

    from_ts = request.args.get("from")
    to_ts = request.args.get("to")

    rows = db.get_checks(
        limit=limit,
        page=page,
        status_filter=status_filter,
        from_ts=from_ts,
        to_ts=to_ts,
    )
    return jsonify({
        "success": True,
        "page": page,
        "limit": limit,
        "count": len(rows),
        "data": [_serialise(r) for r in rows],
    })


@app.route("/api/stats")
def api_stats():
    """Return uptime and response-time statistics."""
    all_time = db.get_stats()
    last_24h = db.get_stats(hours=24)
    last_7d = db.get_stats(hours=168)
    last_30d = db.get_stats(hours=720)
    consecutive_failures = db.get_consecutive_failures()

    return jsonify({
        "success": True,
        "target_url": TARGET_URL,
        "all_time": _serialise(all_time),
        "last_24h": _serialise(last_24h),
        "last_7d": _serialise(last_7d),
        "last_30d": _serialise(last_30d),
        "consecutive_failures": consecutive_failures,
    })


@app.route("/api/check-now", methods=["POST"])
@_require_token
def api_check_now():
    """
    Trigger an immediate monitoring check.
    Protected by Bearer token (MANUAL_CHECK_TOKEN).
    """
    log.info("[API] Manual check triggered by authenticated request")
    result = scheduler.trigger_now()
    return jsonify({"success": True, "data": _serialise(result)})


# ── Error handlers ─────────────────────────────────────────────────────────
@app.errorhandler(401)
def unauthorized(_):
    return jsonify({"error": "Unauthorized — provide a valid Bearer token"}), 401


@app.errorhandler(403)
def forbidden(_):
    return jsonify({"error": "Forbidden — invalid token"}), 403


@app.errorhandler(404)
def not_found(_):
    return jsonify({"error": "Not found"}), 404


@app.errorhandler(500)
def server_error(_):
    return jsonify({"error": "Internal server error"}), 500


# ── Entry point ────────────────────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    log.info("Starting AuraXL Uptime Monitor on port %d", port)
    app.run(host="0.0.0.0", port=port, debug=False)
