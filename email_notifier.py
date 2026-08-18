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

log = logging.getLogger(__name__)

SMTP_SERVER = os.environ.get("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
ALERT_EMAIL = os.environ.get("ALERT_EMAIL", "31pranav104@gmail.com")
EMAIL_ALERTS_ENABLED = os.environ.get("EMAIL_ALERTS_ENABLED", "true").lower() == "true"

def send_outage_email(target_url: str, error_type: str, error_message: str, response_time_ms: int = None, recipient: str = None) -> bool:
    """Dispatches a branded HTML email alert."""
    if not EMAIL_ALERTS_ENABLED:
        log.info("[Email] Alerts disabled via EMAIL_ALERTS_ENABLED=false")
        return False

    to_addr = recipient or ALERT_EMAIL
    if not to_addr:
        return False

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    
    subject = f"🚨 CRITICAL ALERT: {target_url} is DOWN ({error_type or 'Outage Detected'})"
    
    html_body = f"""
    <!DOCTYPE html>
    <html>
    <head>
      <meta charset="utf-8">
      <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background-color: #0b132b; margin: 0; padding: 20px; color: #ffffff; }}
        .card {{ max-width: 600px; margin: 0 auto; background-color: #1c2541; border-radius: 12px; overflow: hidden; border: 1px solid #3a506b; }}
        .header {{ background: linear-gradient(135deg, #ef4444, #b91c1c); padding: 24px; text-align: center; }}
        .header h1 {{ margin: 0; font-size: 22px; color: #ffffff; }}
        .content {{ padding: 24px; }}
        .badge {{ display: inline-block; background-color: #f87171; color: #7f1d1d; font-weight: bold; padding: 4px 12px; border-radius: 9999px; font-size: 12px; margin-bottom: 16px; }}
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
          <h1>🚨 AuraXL Uptime Alert</h1>
          <p style="margin: 6px 0 0 0; font-size: 14px; color: #fee2e2;">Target Website Availability Incident</p>
        </div>
        <div class="content">
          <span class="badge">STATUS: DOWN / INCIDENT</span>
          <p style="font-size: 15px; line-height: 1.5; color: #e2e8f0;">
            AuraXL 24/7 Monitoring detected that <strong>{target_url}</strong> is currently unreachable or rejecting connections.
          </p>
          <div class="metric-box">
            <div class="metric-row">
              <span class="metric-label">Target Website:</span>
              <span class="metric-val">{target_url}</span>
            </div>
            <div class="metric-row">
              <span class="metric-label">Incident Time:</span>
              <span class="metric-val">{timestamp}</span>
            </div>
            <div class="metric-row">
              <span class="metric-label">Error Classification:</span>
              <span class="metric-val">{error_type or 'Connection Outage'}</span>
            </div>
            <div class="metric-row">
              <span class="metric-label">Error Details:</span>
              <span class="metric-val" style="color: #fca5a5;">{error_message or 'No response'}</span>
            </div>
          </div>
          <div class="solution-box">
            <h3>🤖 AI Diagnostic & Non-Coding Remedy</h3>
            <p>
              1. <strong>Hostinger / Cloudflare SSL Mode:</strong> If this is an SSL EOF error, log in to Hostinger/Cloudflare and change SSL mode from <em>Full (Strict)</em> to <em>Full</em> or re-issue SSL certificate.<br>
              2. <strong>Hosting Support:</strong> Check if origin server IP is accepting connections on Port 443.
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

    if not SMTP_USER or not SMTP_PASSWORD:
        log.warning("[Email] Outage alert simulated for %s (SMTP_USER / SMTP_PASSWORD not set in environment).", to_addr)
        return True

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = f"AuraXL Monitor <{SMTP_USER}>"
        msg["To"] = to_addr
        msg.attach(MIMEText(html_body, "html"))

        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=10) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.sendmail(SMTP_USER, [to_addr], msg.as_string())
        
        log.info("[Email] Outage alert sent successfully to %s", to_addr)
        return True
    except Exception as e:
        log.error("[Email] Failed to send email alert: %s", e)
        return False
