"""
email_notifier.py — Automated Email Alert Engine for AuraXL Uptime Monitor.
Dispatches formatted HTML alerts when downtime is detected.
"""

import os
import smtplib
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timezone
import db

log = logging.getLogger(__name__)

DEFAULT_SMTP_USER = "somwanshipranav495@gmail.com"
DEFAULT_SMTP_PASS = "yvbkynezngoxgqfy"

def get_smtp_config():
    """Reads SMTP configuration from DB settings first, then environment / defaults."""
    return {
        "server": db.get_setting("smtp_server", os.environ.get("SMTP_SERVER", "smtp.gmail.com")),
        "port": int(db.get_setting("smtp_port", os.environ.get("SMTP_PORT", "465"))),
        "user": db.get_setting("smtp_user", os.environ.get("SMTP_USER", DEFAULT_SMTP_USER)),
        "password": db.get_setting("smtp_password", os.environ.get("SMTP_PASSWORD", DEFAULT_SMTP_PASS)),
        "alert_email": db.get_setting("alert_email", os.environ.get("ALERT_EMAIL", "31pranav104@gmail.com")),
        "enabled": db.get_setting("email_alerts_enabled", os.environ.get("EMAIL_ALERTS_ENABLED", "true")).lower() == "true"
    }

def send_outage_email(target_url: str, error_type: str, error_message: str, response_time_ms: int = None, recipient: str = None) -> tuple:
    """
    Dispatches a branded HTML email alert using robust SSL/TLS connections.
    Returns (success: bool, message: str).
    """
    cfg = get_smtp_config()

    if not cfg["enabled"]:
        log.info("[Email] Alerts disabled in settings.")
        return False, "Email alerts are disabled in Settings."

    to_addr = recipient or cfg["alert_email"]
    if not to_addr:
        return False, "No recipient email address configured."

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    is_test = "TEST" in str(error_type).upper()
    
    if is_test:
        subject = f"✅ AuraXL Monitor — Verified Alert Delivery to {to_addr}"
    else:
        subject = f"🚨 CRITICAL ALERT: {target_url} is DOWN ({error_type or 'Outage Detected'})"
    
    html_body = f"""
    <!DOCTYPE html>
    <html>
    <head>
      <meta charset="utf-8">
      <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background-color: #0b132b; margin: 0; padding: 20px; color: #ffffff; }}
        .card {{ max-width: 600px; margin: 0 auto; background-color: #1c2541; border-radius: 12px; overflow: hidden; border: 1px solid #3a506b; }}
        .header {{ background: linear-gradient(135deg, {'#0DB2A7, #2BC0D4' if is_test else '#ef4444, #b91c1c'}); padding: 24px; text-align: center; }}
        .header h1 {{ margin: 0; font-size: 22px; color: #ffffff; }}
        .content {{ padding: 24px; }}
        .badge {{ display: inline-block; background-color: {'#d1fae5' if is_test else '#f87171'}; color: {'#065f46' if is_test else '#7f1d1d'}; font-weight: bold; padding: 4px 12px; border-radius: 9999px; font-size: 12px; margin-bottom: 16px; }}
        .metric-box {{ background-color: #0b132b; border-radius: 8px; padding: 16px; margin-bottom: 20px; }}
        .metric-row {{ display: flex; justify-content: space-between; padding: 6px 0; border-bottom: 1px solid #1c2541; font-size: 14px; }}
        .metric-label {{ color: #85D8D2; font-weight: 500; }}
        .metric-val {{ color: #ffffff; font-weight: 600; font-family: monospace; }}
        .solution-box {{ background-color: #0e3b43; border-left: 4px solid #2BC0D4; padding: 16px; border-radius: 4px; margin-top: 20px; }}
        .solution-box h3 {{ margin-top: 0; color: #2BC0D4; font-size: 15px; }}
        .solution-box p {{ margin: 0; font-size: 13px; color: #e2e8f0; line-height: 1.5; }}
        .footer {{ text-align: center; padding: 16px; font-size: 12px; color: #64748b; background-color: #0b132b; }}
      </style>
    </head>
    <body>
      <div class="card">
        <div class="header">
          <h1>{'✅ AuraXL Guardian Alert Test' if is_test else '🚨 AuraXL Uptime Alert'}</h1>
          <p style="margin: 6px 0 0 0; font-size: 14px; color: #fee2e2;">Target Website Availability Guardian</p>
        </div>
        <div class="content">
          <span class="badge">{'STATUS: SYSTEM OPERATIONAL' if is_test else 'STATUS: DOWN / INCIDENT'}</span>
          <p style="font-size: 15px; line-height: 1.5; color: #e2e8f0;">
            {'This is a verification alert confirming that AuraXL automated email notifications are fully active and connected.' if is_test else f'AuraXL 24/7 Monitoring detected that <strong>{target_url}</strong> is currently unreachable or rejecting connections.'}
          </p>
          <div class="metric-box">
            <div class="metric-row">
              <span class="metric-label">Target Website:</span>
              <span class="metric-val">{target_url}</span>
            </div>
            <div class="metric-row">
              <span class="metric-label">Timestamp:</span>
              <span class="metric-val">{timestamp}</span>
            </div>
            <div class="metric-row">
              <span class="metric-label">Status Details:</span>
              <span class="metric-val" style="color: {'#34d399' if is_test else '#fca5a5'};">{error_message or 'Website probe completed'}</span>
            </div>
          </div>
          <div class="solution-box">
            <h3>🤖 Automated AI Monitoring Active</h3>
            <p>
              When an outage occurs, AuraXL dispatches instant alerts and root-cause solutions directly to your inbox.
            </p>
          </div>
        </div>
        <div class="footer">
          AuraXL Standalone Uptime Monitor • 24/7 Cloud Guardian
        </div>
      </div>
    </body>
    </html>
    """

    user = cfg["user"] or DEFAULT_SMTP_USER
    password = (cfg["password"] or DEFAULT_SMTP_PASS).replace(" ", "")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"AuraXL Guardian <{user}>"
    msg["To"] = to_addr
    msg.attach(MIMEText(html_body, "html"))

    # Strategy: Try Port 465 SSL first (standard for cloud servers/Render), fallback to 587 STARTTLS
    ports_to_try = [465, 587]
    last_err = None

    for port in ports_to_try:
        try:
            if port == 465:
                with smtplib.SMTP_SSL(cfg["server"], port, timeout=12) as server:
                    server.login(user, password)
                    server.sendmail(user, [to_addr], msg.as_string())
            else:
                with smtplib.SMTP(cfg["server"], port, timeout=12) as server:
                    server.starttls()
                    server.login(user, password)
                    server.sendmail(user, [to_addr], msg.as_string())

            log.info("[Email] Outage alert sent successfully to %s via Port %d", to_addr, port)
            return True, f"Email delivered successfully to {to_addr}!"
        except Exception as e:
            last_err = e
            log.warning("[Email] Port %d delivery attempt failed: %s", port, e)

    log.error("[Email] All SMTP delivery attempts failed: %s", last_err)
    return False, f"SMTP Error: {str(last_err)}"
