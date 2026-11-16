"""SMS delivery. Simulated by default; real Twilio delivery needs every safeguard below to be satisfied.

Live sending requires ALL of: SMS_MODE=live, SMS_ALLOW_LIVE=true, Twilio credentials configured, and an
explicit confirm flag on the individual request. Anything else is either simulated (mode=simulated) or
blocked and logged (mode=live without the safeguards) - nothing is ever sent automatically.
"""
from __future__ import annotations

from datetime import datetime, timezone

import requests

from .config import Settings

TWILIO_URL = "https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json"


def send_alert_sms(settings: Settings, conn, alert: dict, confirm_live: bool = False) -> dict:
    if not alert.get("sms_body"):
        raise ValueError("This alert is below the SMS threshold and has no message to send.")
    mode, status, sid, error = settings.sms_mode, "simulated", None, None
    if mode == "live":
        if not settings.live_sms_possible:
            status, error = "blocked", "Live SMS is not fully configured (needs SMS_ALLOW_LIVE=true and Twilio credentials)."
        elif not confirm_live:
            status, error = "blocked", "Live SMS needs an explicit confirmation on this request (confirm_live=true)."
        else:
            try:
                resp = requests.post(TWILIO_URL.format(sid=settings.twilio_account_sid), timeout=10,
                                     auth=(settings.twilio_account_sid, settings.twilio_auth_token),
                                     data={"To": alert["to_number"], "From": settings.twilio_from_number, "Body": alert["sms_body"]})
                if resp.status_code in (200, 201):
                    status, sid = "sent", resp.json().get("sid")
                else:
                    status, error = "failed", f"Twilio returned HTTP {resp.status_code}"
            except requests.RequestException as exc:
                status, error = "failed", f"Could not reach Twilio ({type(exc).__name__})"
    created = datetime.now(timezone.utc).isoformat(timespec="seconds")
    cur = conn.execute(
        "INSERT INTO sms_log(created_at, alert_key, clinic_id, to_number, body, mode, status, provider_sid, error) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (created, alert["key"], alert["clinic_id"], alert["to_number"], alert["sms_body"], mode, status, sid, error))
    return {"id": cur.lastrowid, "created_at": created, "alert_key": alert["key"], "clinic_id": alert["clinic_id"],
            "to_number": alert["to_number"], "body": alert["sms_body"], "mode": mode, "status": status,
            "provider_sid": sid, "error": error}
