"""
push_notifier.py — VAPID Web Push notification delivery for AuraXL Uptime Monitor.

Sends real server-side push messages to all subscribed browsers/devices
even when the dashboard tab is closed, using the Web Push Protocol (RFC 8030).
"""

import json
import logging
import os

log = logging.getLogger(__name__)

# VAPID keys — set as environment variables on Render
# VAPID_PUBLIC_KEY  → URL-safe base64 public key (used in frontend)
# VAPID_PRIVATE_KEY → URL-safe base64 private key  (kept server-side)
VAPID_PUBLIC_KEY = os.environ.get("VAPID_PUBLIC_KEY", "")
VAPID_PRIVATE_KEY = os.environ.get("VAPID_PRIVATE_KEY", "")
VAPID_CLAIM_EMAIL = os.environ.get("VAPID_CLAIM_EMAIL", "31pranav104@gmail.com")


def send_push_to_subscription(subscription_info: dict, title: str, body: str, icon: str = "/static/icons/icon-192x192.png") -> bool:
    """
    Send a Web Push notification to one subscription endpoint.
    Returns True on success, False on failure.
    """
    if not VAPID_PUBLIC_KEY or not VAPID_PRIVATE_KEY:
        log.warning("[Push] VAPID keys not configured — cannot send push notification.")
        return False

    try:
        from pywebpush import webpush, WebPushException

        payload = json.dumps({
            "title": title,
            "body": body,
            "icon": icon,
            "badge": "/static/icons/icon-72x72.png",
            "vibrate": [200, 100, 200],
            "data": {"url": "/"}
        })

        webpush(
            subscription_info=subscription_info,
            data=payload,
            vapid_private_key=VAPID_PRIVATE_KEY,
            vapid_claims={
                "sub": f"mailto:{VAPID_CLAIM_EMAIL}"
            }
        )
        log.info("[Push] Sent push notification: %s", title)
        return True

    except Exception as e:
        err_str = str(e)
        # 410 Gone = subscription expired/revoked — caller should remove it
        if "410" in err_str or "404" in err_str:
            log.info("[Push] Subscription expired (410/404) — should be removed.")
            raise  # re-raise so caller can clean it up
        log.warning("[Push] Push delivery failed: %s", err_str)
        return False


def send_push_to_all(subscriptions: list, title: str, body: str) -> tuple:
    """
    Send a push notification to all stored subscriptions.
    Returns (success_count, expired_endpoints_list).
    """
    success = 0
    expired = []
    for sub in subscriptions:
        try:
            ok = send_push_to_subscription(sub, title, body)
            if ok:
                success += 1
        except Exception as e:
            if "410" in str(e) or "404" in str(e):
                endpoint = sub.get("endpoint", "")
                if endpoint:
                    expired.append(endpoint)
    return success, expired
