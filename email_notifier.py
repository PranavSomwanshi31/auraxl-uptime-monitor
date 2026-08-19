"""
email_notifier.py — Enterprise Automated Email Engine for AuraXL Uptime Monitor.

Features:
  - Multi-recipient dynamic dispatch (RFC 5322 validation, comma/semicolon/space-separated).
  - Multi-port IPv4 fallback: Port 465 (SSL) -> Port 587 (STARTTLS) -> Port 2525 (Alternative).
  - Deep connection diagnosis & network health checking.
  - Safe error logging without exposing credentials or passwords.
"""

import os
import re
import time
import smtplib
import socket
import ssl
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timezone
import db

log = logging.getLogger(__name__)

DEFAULT_SMTP_SERVER = "smtp.gmail.com"
DEFAULT_SMTP_USER   = "somwanshipranav495@gmail.com"
DEFAULT_SMTP_PASS   = "yvbkynezngoxgqfy"


def parse_recipients(addr_string: str) -> list:
    """Extract and validate all email addresses from a delimiter string."""
    if not addr_string:
        return []
    raw_tokens = re.split(r'[,;\s\n\r]+', addr_string.strip())
    valid_emails = []
    # RFC 5322 simplified email pattern
    pattern = re.compile(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$")
    for token in raw_tokens:
        clean = token.strip()
        if clean and pattern.match(clean):
            if clean not in valid_emails:
                valid_emails.append(clean)
    return valid_emails


def get_smtp_config():
    """Reads SMTP configuration from DB settings first, then environment / defaults."""
    return {
        "server": db.get_setting("smtp_server", os.environ.get("SMTP_SERVER", DEFAULT_SMTP_SERVER)).strip(),
        "port": int(db.get_setting("smtp_port", os.environ.get("SMTP_PORT", "465"))),
        "user": db.get_setting("smtp_user", os.environ.get("SMTP_USER", DEFAULT_SMTP_USER)).strip(),
        "password": db.get_setting("smtp_password", os.environ.get("SMTP_PASSWORD", DEFAULT_SMTP_PASS)).strip(),
        "alert_email": db.get_setting("alert_email", os.environ.get("ALERT_EMAIL", "")).strip(),
        "enabled": db.get_setting("email_alerts_enabled", os.environ.get("EMAIL_ALERTS_ENABLED", "true")).lower() == "true"
    }


def diagnose_smtp_connection(host: str = None, user: str = None, password: str = None) -> dict:
    """
    Performs a deep 4-step network and SMTP diagnostic without logging secrets:
      1. DNS Resolution
      2. TCP Reachability (Ports 465, 587, 2525)
      3. SSL/TLS Handshake
      4. SMTP Authentication
    """
    cfg = get_smtp_config()
    server_host = host or cfg["server"]
    smtp_user = user or cfg["user"]
    smtp_pass = (password or cfg["password"]).replace(" ", "")

    diag = {
        "host": server_host,
        "sender": smtp_user,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "dns": {},
        "ports": {},
        "auth": {},
        "recommended_port": 465,
        "overall_status": "UNKNOWN"
    }

    # Step 1: DNS Resolution
    t0 = time.perf_counter()
    try:
        addr_info = socket.getaddrinfo(server_host, 465, socket.AF_INET, socket.SOCK_STREAM)
        ips = [x[4][0] for x in addr_info]
        diag["dns"] = {
            "status": "PASS",
            "resolved_ips": ips,
            "latency_ms": round((time.perf_counter() - t0) * 1000)
        }
        log.info("[SMTP Diag] DNS resolved for %s: %s", server_host, ips)
    except Exception as e:
        diag["dns"] = {"status": "FAIL", "error": str(e)}
        diag["overall_status"] = "FAIL_DNS"
        log.error("[SMTP Diag] DNS resolution failed for %s: %s", server_host, e)
        return diag

    # Step 2: Port Checks (465 SSL, 587 STARTTLS, 2525)
    for p in [465, 587, 2525]:
        t0 = time.perf_counter()
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(5.0)
            sock.connect((server_host, p))
            sock.close()
            t_ms = round((time.perf_counter() - t0) * 1000)
            diag["ports"][str(p)] = {"status": "PASS", "latency_ms": t_ms}
            log.info("[SMTP Diag] TCP Port %d is REACHABLE (%dms)", p, t_ms)
        except Exception as e:
            diag["ports"][str(p)] = {"status": "FAIL", "error": str(e)}
            log.warning("[SMTP Diag] TCP Port %d is UNREACHABLE: %s", p, e)

    # Step 3 & 4: SSL/TLS Connection & Authentication
    if diag["ports"].get("465", {}).get("status") == "PASS":
        try:
            with smtplib.SMTP_SSL(server_host, 465, timeout=10) as server:
                code, resp = server.login(smtp_user, smtp_pass)
                diag["auth"] = {"status": "PASS", "method": "Port 465 SSL", "code": code}
                diag["overall_status"] = "PASS"
                diag["recommended_port"] = 465
                log.info("[SMTP Diag] Authentication SUCCESS via Port 465 SSL")
                return diag
        except smtplib.SMTPAuthenticationError as e:
            diag["auth"] = {"status": "FAIL_AUTH", "error": "Invalid email or Google App Password"}
            diag["overall_status"] = "FAIL_AUTH"
            log.error("[SMTP Diag] Authentication failed: %s", e)
            return diag
        except Exception as e:
            diag["auth"] = {"status": "FAIL_CONNECT", "error": str(e)}
            log.warning("[SMTP Diag] Port 465 auth check error: %s", e)

    if diag["ports"].get("587", {}).get("status") == "PASS":
        try:
            with smtplib.SMTP(server_host, 587, timeout=10) as server:
                server.starttls()
                code, resp = server.login(smtp_user, smtp_pass)
                diag["auth"] = {"status": "PASS", "method": "Port 587 STARTTLS", "code": code}
                diag["overall_status"] = "PASS"
                diag["recommended_port"] = 587
                log.info("[SMTP Diag] Authentication SUCCESS via Port 587 STARTTLS")
                return diag
        except Exception as e:
            diag["auth"] = {"status": "FAIL", "error": str(e)}
            log.warning("[SMTP Diag] Port 587 auth check error: %s", e)

    diag["overall_status"] = "BLOCKED_NETWORK" if all(v.get("status") == "FAIL" for v in diag["ports"].values()) else "FAIL"
    return diag


def connect_smtp_ipv4(host: str, port: int, timeout: int = 12):
    """
    Force IPv4 socket resolution before connecting.
    Prevents Linux cloud container routing failures like:
    [Errno 101] Network is unreachable (which occurs when IPv6 is attempted first).
    """
    last_err = None
    try:
        addr_info = socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_STREAM)
        for res in addr_info:
            af, socktype, proto, canonname, sa = res
            try:
                raw_sock = socket.socket(af, socktype, proto)
                raw_sock.settimeout(timeout)
                raw_sock.connect(sa)

                if port == 465:
                    ctx = ssl.create_default_context()
                    ssl_sock = ctx.wrap_socket(raw_sock, server_hostname=host)
                    server = smtplib.SMTP_SSL(timeout=timeout)
                    server.sock = ssl_sock
                    server.file = smtplib.SSLFakeFile(ssl_sock)
                    (code, msg) = server.getreply()
                    if code == 220:
                        return server
                else:
                    server = smtplib.SMTP(timeout=timeout)
                    server.sock = raw_sock
                    server.file = smtplib.SSLFakeFile(raw_sock)
                    (code, msg) = server.getreply()
                    if code == 220:
                        server.starttls()
                        return server
            except Exception as e:
                last_err = e
                continue
    except Exception as e:
        last_err = e

    # Fallback standard
    if port == 465:
        return smtplib.SMTP_SSL(host, port, timeout=timeout)
    else:
        server = smtplib.SMTP(host, port, timeout=timeout)
        server.starttls()
        return server


def send_outage_email(target_url: str, error_type: str, error_message: str, response_time_ms: int = None, recipient: str = None) -> tuple:
    """
    Dispatches a branded HTML email alert to all dynamic recipient addresses.
    Supports single or multiple comma-separated emails.
    Returns (success: bool, message: str).
    """
    cfg = get_smtp_config()

    if not cfg["enabled"]:
        log.info("[Email] Alerts are disabled in Settings.")
        return False, "Email alerts are disabled in Settings."

    raw_recipients = recipient if recipient is not None else cfg["alert_email"]
    recipient_list = parse_recipients(raw_recipients)

    if not recipient_list:
        log.warning("[Email] No valid recipient email address found in: %r", raw_recipients)
        return False, "No valid recipient email address entered. Please provide a valid email format."

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    is_test = "TEST" in str(error_type).upper()
    recipients_display = ", ".join(recipient_list)
    
    if is_test:
        subject = f"✅ AuraXL Monitor — Verified Alert Delivery to [{recipients_display}]"
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
          <h1>{'✅ AuraXL Guardian Alert Verification' if is_test else '🚨 AuraXL Uptime Incident Alert'}</h1>
          <p style="margin: 6px 0 0 0; font-size: 14px; color: #fee2e2;">Target Website Availability Guardian</p>
        </div>
        <div class="content">
          <span class="badge">{'STATUS: SYSTEM OPERATIONAL' if is_test else 'STATUS: DOWN / INCIDENT'}</span>
          <p style="font-size: 15px; line-height: 1.5; color: #e2e8f0;">
            {'This is an automated verification email confirming that AuraXL incident notifications are active and successfully delivered to your inbox.' if is_test else f'AuraXL 24/7 Monitoring detected that <strong>{target_url}</strong> is currently unreachable or rejecting connections.'}
          </p>
          <div class="metric-box">
            <div class="metric-row">
              <span class="metric-label">Target Website:</span>
              <span class="metric-val">{target_url}</span>
            </div>
            <div class="metric-row">
              <span class="metric-label">Alert Recipient(s):</span>
              <span class="metric-val">{recipients_display}</span>
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
            <h3>🤖 Automated AI Monitoring & Recovery Active</h3>
            <p>
              When an outage or SLA degradation occurs, AuraXL dispatches instant root-cause diagnostics to all registered recipient emails.
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
    msg["To"] = ", ".join(recipient_list)
    msg.attach(MIMEText(html_body, "html"))

    ports_to_try = [465, 587, 2525]
    last_err = None

    for port in ports_to_try:
        try:
            log.info("[Email] Attempting dispatch to [%s] via %s:%d", recipients_display, cfg["server"], port)
            server = connect_smtp_ipv4(cfg["server"], port, timeout=12)
            server.login(user, password)
            server.sendmail(user, recipient_list, msg.as_string())
            server.quit()
            log.info("[Email] Outage alert sent successfully to %s via Port %d", recipients_display, port)
            return True, f"Email delivered successfully to {recipients_display}!"
        except smtplib.SMTPAuthenticationError as auth_err:
            log.error("[Email] SMTP Authentication failed: %s", auth_err)
            return False, "SMTP Authentication Failed: Please check your Gmail address and 16-character Google App Password in Settings."
        except Exception as e:
            last_err = e
            log.warning("[Email] Port %d delivery attempt failed: %s", port, e)

    log.error("[Email] All SMTP delivery attempts to %s failed: %s", recipients_display, last_err)
    
    err_msg = str(last_err)
    if "101" in err_msg or "unreachable" in err_msg.lower():
        user_friendly_msg = "Network Error: Outbound SMTP connection is restricted by the cloud hosting firewall. Please check SMTP host and port."
    elif "timeout" in err_msg.lower() or "timed out" in err_msg.lower():
        user_friendly_msg = "Connection Timeout: Unable to reach SMTP server on ports 465/587. Please verify server internet routing."
    else:
        user_friendly_msg = f"SMTP Error: {err_msg}"
        
    return False, user_friendly_msg
