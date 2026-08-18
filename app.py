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
    row = db.get_latest_check(TARGET_URL)
    if row is None:
        return jsonify({
            "success": True,
            "data": None,
            "message": "No checks performed yet",
        })
    return jsonify({"success": True, "data": _serialise(row)})


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
