import os
import re
import time
import json
import smtplib
import socket
import ssl
import logging
import urllib.request
import urllib.error
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timezone
import db

log = logging.getLogger(__name__)

DEFAULT_SMTP_SERVER = 'smtp.gmail.com'
DEFAULT_SMTP_USER   = 'somwanshipranav495@gmail.com'
DEFAULT_SMTP_PASS   = 'yvbkynezngoxgqfy'

RESEND_API_ENDPOINT = 'https://api.resend.com/emails'
EMAIL_PATTERN = re.compile(r'^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$')


def parse_recipients(addr_string):
    if not addr_string:
        return []
    raw_tokens = re.split(r'[,;\s\n\r]+', addr_string.strip())
    valid_emails = []
    for token in raw_tokens:
        clean = token.strip()
        if clean and EMAIL_PATTERN.match(clean) and clean not in valid_emails:
            valid_emails.append(clean)
    return valid_emails


def get_smtp_config():
    return {
        'server':      db.get_setting('smtp_server',   os.environ.get('SMTP_SERVER',   DEFAULT_SMTP_SERVER)).strip(),
        'port':        int(db.get_setting('smtp_port', os.environ.get('SMTP_PORT',     '465'))),
        'user':        db.get_setting('smtp_user',     os.environ.get('SMTP_USER',     DEFAULT_SMTP_USER)).strip(),
        'password':    db.get_setting('smtp_password', os.environ.get('SMTP_PASSWORD', DEFAULT_SMTP_PASS)).strip(),
        'alert_email': db.get_setting('alert_email',   os.environ.get('ALERT_EMAIL',   '')).strip(),
        'enabled':     db.get_setting('email_alerts_enabled', os.environ.get('EMAIL_ALERTS_ENABLED', 'true')).lower() == 'true',
    }


def get_resend_config():
    return {
        'api_key':    os.environ.get('RESEND_API_KEY', '').strip(),
        'from_email': os.environ.get('RESEND_FROM_EMAIL', 'AuraXL Guardian <onboarding@resend.dev>').strip(),
    }


def is_resend_configured():
    return bool(get_resend_config()['api_key'])


def send_via_resend(recipient_list, subject, html_body):
    rc = get_resend_config()
    if not rc['api_key']:
        return False, 'Resend API key not configured.'
    recipients_display = ', '.join(recipient_list)
    log.info('[Email/Resend] Dispatching to [%s] via HTTPS API', recipients_display)
    payload = json.dumps({
        'from':    rc['from_email'],
        'to':      recipient_list,
        'subject': subject,
        'html':    html_body,
    }).encode('utf-8')
    api_key = rc['api_key']
    req = urllib.request.Request(
        RESEND_API_ENDPOINT,
        data=payload,
        headers={
            'Authorization': 'Bearer ' + api_key,
            'Content-Type':  'application/json',
        },
        method='POST',
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            resp_body = resp.read().decode('utf-8')
            log.info('[Email/Resend] Delivered to %s. Response: %s', recipients_display, resp_body[:120])
            return True, f'Email delivered successfully to {recipients_display}!'
    except urllib.error.HTTPError as e:
        err_body = e.read().decode('utf-8') if e.fp else str(e)
        log.error('[Email/Resend] HTTP %d error: %s', e.code, err_body[:200])
        if e.code == 401:
            return False, 'Resend API Authentication Failed: Please check your RESEND_API_KEY on Render.'
        elif e.code == 422:
            return False, f'Resend API Validation Error: {err_body[:200]}'
        else:
            return False, f'Resend API Error (HTTP {e.code}): {err_body[:200]}'
    except Exception as e:
        log.error('[Email/Resend] Error: %s', e)
        return False, f'Resend API connection error: {str(e)}'


def connect_smtp_ipv4(host, port, timeout=12):
    try:
        addr_info = socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_STREAM)
        for res in addr_info:
            af, socktype, proto, _, sa = res
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
                    code, _ = server.getreply()
                    if code == 220:
                        return server
                else:
                    server = smtplib.SMTP(timeout=timeout)
                    server.sock = raw_sock
                    server.file = smtplib.SSLFakeFile(raw_sock)
                    code, _ = server.getreply()
                    if code == 220:
                        server.starttls()
                        return server
            except Exception:
                continue
    except Exception:
        pass
    if port == 465:
        return smtplib.SMTP_SSL(host, port, timeout=timeout)
    else:
        server = smtplib.SMTP(host, port, timeout=timeout)
        server.starttls()
        return server


def send_via_smtp(recipient_list, subject, html_body):
    cfg = get_smtp_config()
    user     = cfg['user'] or DEFAULT_SMTP_USER
    password = (cfg['password'] or DEFAULT_SMTP_PASS).replace(' ', '')
    recipients_display = ', '.join(recipient_list)
    msg = MIMEMultipart('alternative')
    msg['Subject'] = subject
    msg['From']    = f'AuraXL Guardian <{user}>'
    msg['To']      = ', '.join(recipient_list)
    msg.attach(MIMEText(html_body, 'html'))
    for port in [465, 587]:
        try:
            log.info('[Email/SMTP] Attempting to [%s] via %s:%d', recipients_display, cfg['server'], port)
            server = connect_smtp_ipv4(cfg['server'], port, timeout=12)
            server.login(user, password)
            server.sendmail(user, recipient_list, msg.as_string())
            server.quit()
            log.info('[Email/SMTP] Delivered to %s via Port %d', recipients_display, port)
            return True, f'Email delivered successfully to {recipients_display}!'
        except smtplib.SMTPAuthenticationError:
            log.error('[Email/SMTP] Authentication failed')
            return False, 'SMTP Authentication Failed: Verify your Gmail address and 16-character Google App Password in Settings.'
        except (socket.timeout, TimeoutError, OSError) as e:
            log.warning('[Email/SMTP] Port %d unreachable: %s', port, e)
            continue
        except Exception as e:
            log.warning('[Email/SMTP] Port %d error: %s', port, e)
            continue
    return False, (
        'SMTP Connection Blocked: Outbound SMTP ports 465 and 587 are unreachable from this server. '
        'To fix this on Render, add a RESEND_API_KEY environment variable in your Render dashboard.'
    )


def send_outage_email(target_url, error_type, error_message, response_time_ms=None, recipient=None):
    cfg = get_smtp_config()
    if not cfg['enabled']:
        return False, 'Email alerts are disabled in Settings.'
    raw_recipients = recipient if recipient is not None else cfg['alert_email']
    recipient_list = parse_recipients(raw_recipients)
    if not recipient_list:
        return False, 'No valid recipient email address entered. Please enter a properly formatted email address.'
    timestamp          = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')
    is_test            = 'TEST' in str(error_type).upper()
    recipients_display = ', '.join(recipient_list)
    delivery_mode      = 'Resend HTTPS API' if is_resend_configured() else 'SMTP'
    if is_test:
        subject = 'AuraXL Monitor - Alert Verification [' + recipients_display + ']'
    else:
        subject = 'ALERT: ' + target_url + ' is DOWN (' + (error_type or 'Outage Detected') + ')'
    html_body = _build_email_html(target_url, recipients_display, timestamp, is_test, error_message, delivery_mode)
    if is_resend_configured():
        log.info('[Email] Using Resend HTTPS API to [%s]', recipients_display)
        return send_via_resend(recipient_list, subject, html_body)
    else:
        log.info('[Email] Using SMTP to [%s]', recipients_display)
        return send_via_smtp(recipient_list, subject, html_body)


def diagnose_smtp_connection(host=None, user=None, password=None):
    cfg         = get_smtp_config()
    server_host = (host or cfg['server']).strip()
    smtp_user   = user or cfg['user']
    smtp_pass   = (password or cfg['password']).replace(' ', '')
    diag = {
        'host':               server_host,
        'sender':             smtp_user,
        'delivery_mode':      'resend_https' if is_resend_configured() else 'smtp',
        'resend_configured':  is_resend_configured(),
        'timestamp':          datetime.now(timezone.utc).isoformat(),
        'dns':                {},
        'ports':              {},
        'auth':               {},
        'resend_reachability':{},
        'overall_status':     'UNKNOWN',
        'recommended_action': '',
    }
    # DNS
    t0 = time.perf_counter()
    try:
        addr_info = socket.getaddrinfo(server_host, 465, socket.AF_INET, socket.SOCK_STREAM)
        ips = [x[4][0] for x in addr_info]
        diag['dns'] = {'status': 'PASS', 'resolved_ips': ips, 'latency_ms': round((time.perf_counter() - t0)*1000)}
        log.info('[SMTP Diag] DNS %s -> %s', server_host, ips)
    except Exception as e:
        diag['dns'] = {'status': 'FAIL', 'error': str(e)}
        diag['overall_status'] = 'FAIL_DNS'
        return diag
    # Ports
    any_open = False
    for p in [465, 587, 2525]:
        t0 = time.perf_counter()
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(5.0)
            sock.connect((server_host, p))
            sock.close()
            t_ms = round((time.perf_counter() - t0)*1000)
            diag['ports'][str(p)] = {'status': 'PASS', 'latency_ms': t_ms}
            any_open = True
            log.info('[SMTP Diag] Port %d OPEN (%dms)', p, t_ms)
        except socket.timeout:
            diag['ports'][str(p)] = {'status': 'TIMEOUT', 'error': 'Connection timed out — port likely blocked by hosting provider firewall'}
            log.warning('[SMTP Diag] Port %d TIMED OUT', p)
        except Exception as e:
            diag['ports'][str(p)] = {'status': 'FAIL', 'error': str(e)}
    if not any_open:
        diag['auth'] = {
            'status': 'SKIPPED',
            'reason': 'All SMTP ports (465, 587) are unreachable. This is a network/firewall restriction, NOT a credentials problem. Authentication cannot be tested until connectivity is restored.',
        }
        diag['overall_status'] = 'SMTP_BLOCKED'
        diag['recommended_action'] = 'Add RESEND_API_KEY to Render environment variables to enable HTTPS-based email delivery that bypasses SMTP port restrictions.'
    else:
        for port, mode in [(465, 'SSL'), (587, 'STARTTLS')]:
            if diag['ports'].get(str(port), {}).get('status') != 'PASS':
                continue
            try:
                if port == 465:
                    with smtplib.SMTP_SSL(server_host, port, timeout=10) as server:
                        code, _ = server.login(smtp_user, smtp_pass)
                        diag['auth'] = {'status': 'PASS', 'method': f'Port {port} {mode}', 'code': code}
                        diag['overall_status'] = 'PASS'
                else:
                    with smtplib.SMTP(server_host, port, timeout=10) as server:
                        server.starttls()
                        code, _ = server.login(smtp_user, smtp_pass)
                        diag['auth'] = {'status': 'PASS', 'method': f'Port {port} {mode}', 'code': code}
                        diag['overall_status'] = 'PASS'
                log.info('[SMTP Diag] Auth SUCCESS via Port %d', port)
                break
            except smtplib.SMTPAuthenticationError as e:
                diag['auth'] = {'status': 'FAIL_AUTH', 'error': 'Invalid Gmail address or App Password.'}
                diag['overall_status'] = 'FAIL_AUTH'
                break
            except Exception as e:
                diag['auth'] = {'status': 'FAIL_CONNECT', 'error': str(e)}
    # Resend reachability
    try:
        req2 = urllib.request.Request('https://api.resend.com', headers={'User-Agent': 'AuraXL-Monitor/1.0'})
        with urllib.request.urlopen(req2, timeout=5) as resp:
            diag['resend_reachability'] = {'status': 'PASS', 'http_code': resp.status}
    except urllib.error.HTTPError as e:
        diag['resend_reachability'] = {'status': 'PASS', 'http_code': e.code}
    except Exception as e:
        diag['resend_reachability'] = {'status': 'FAIL', 'error': str(e)}
    return diag


def _build_email_html(target_url, recipients_display, timestamp, is_test, error_message, delivery_mode):
    hg = '#0DB2A7, #2BC0D4' if is_test else '#ef4444, #b91c1c'
    bb = '#d1fae5' if is_test else '#f87171'
    bc = '#065f46' if is_test else '#7f1d1d'
    sl = 'STATUS: SYSTEM OPERATIONAL' if is_test else 'STATUS: DOWN / INCIDENT'
    ht = 'AuraXL Guardian Alert Verification' if is_test else 'AuraXL Uptime Incident Alert'
    dc = '#34d399' if is_test else '#fca5a5'
    it = ('This is an automated verification email confirming that AuraXL incident notifications are active.'
          if is_test else
          f'AuraXL 24/7 Monitoring detected that <strong>{target_url}</strong> is currently unreachable.')
    return f'''<!DOCTYPE html><html><head><meta charset="utf-8"><style>
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#0b132b;margin:0;padding:20px;color:#fff}}
.card{{max-width:600px;margin:0 auto;background:#1c2541;border-radius:12px;overflow:hidden;border:1px solid #3a506b}}
.header{{background:linear-gradient(135deg,{hg});padding:24px;text-align:center}}
.header h1{{margin:0;font-size:22px;color:#fff}}
.content{{padding:24px}}
.badge{{display:inline-block;background:{bb};color:{bc};font-weight:bold;padding:4px 12px;border-radius:9999px;font-size:12px;margin-bottom:16px}}
.metric-box{{background:#0b132b;border-radius:8px;padding:16px;margin-bottom:20px}}
.metric-row{{display:flex;justify-content:space-between;padding:6px 0;border-bottom:1px solid #1c2541;font-size:14px}}
.metric-label{{color:#85D8D2;font-weight:500}}.metric-val{{color:#fff;font-weight:600;font-family:monospace}}
.solution-box{{background:#0e3b43;border-left:4px solid #2BC0D4;padding:16px;border-radius:4px;margin-top:20px}}
.solution-box h3{{margin-top:0;color:#2BC0D4;font-size:15px}}.solution-box p{{margin:0;font-size:13px;color:#e2e8f0;line-height:1.5}}
.footer{{text-align:center;padding:16px;font-size:12px;color:#64748b;background:#0b132b}}
</style></head><body><div class="card">
<div class="header"><h1>{ht}</h1><p style="margin:6px 0 0;font-size:14px;color:#fee2e2">Target Website Availability Guardian</p></div>
<div class="content"><span class="badge">{sl}</span><p style="font-size:15px;line-height:1.5;color:#e2e8f0">{it}</p>
<div class="metric-box">
<div class="metric-row"><span class="metric-label">Target Website:</span><span class="metric-val">{target_url}</span></div>
<div class="metric-row"><span class="metric-label">Alert Recipient(s):</span><span class="metric-val">{recipients_display}</span></div>
<div class="metric-row"><span class="metric-label">Timestamp:</span><span class="metric-val">{timestamp}</span></div>
<div class="metric-row"><span class="metric-label">Delivery Method:</span><span class="metric-val">{delivery_mode}</span></div>
<div class="metric-row"><span class="metric-label">Status Details:</span><span class="metric-val" style="color:{dc}">{error_message or 'Website probe completed'}</span></div>
</div>
<div class="solution-box"><h3>Automated AI Monitoring and Recovery Active</h3>
<p>When an outage or SLA degradation occurs, AuraXL dispatches instant root-cause diagnostics to all registered recipient emails.</p></div>
</div><div class="footer">AuraXL Standalone Uptime Monitor - 24/7 Cloud Guardian</div></div></body></html>'''
