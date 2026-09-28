"""Email one-time codes for farmer sign in / sign up (no passwords).

A 6-digit code is emailed; only an HMAC of it is stored (collection
`otp_codes`, one active code per email + purpose). Codes expire after
OTP_TTL_MINUTES, allow OTP_MAX_ATTEMPTS wrong guesses, and can be re-sent
only after OTP_RESEND_SECONDS.
"""
import hashlib
import hmac
import html
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from flask import current_app

from app.extensions import mongo
from app.services.email_service import send_email

PURPOSES = {"login", "register"}


class OtpError(Exception):
    def __init__(self, code: str, message: str, status: int = 400, details: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.code = code
        self.status = status
        self.details = details


def _col():
    return mongo.db.otp_codes


def ensure_indexes() -> None:
    """TTL index lets MongoDB delete expired codes on its own."""
    _col().create_index("expires_at", expireAfterSeconds=0)
    _col().create_index([("email", 1), ("purpose", 1)], unique=True)


def _now() -> datetime:
    # PyMongo returns naive UTC datetimes, so compare in naive UTC throughout.
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _hash(email: str, purpose: str, code: str) -> str:
    key = current_app.config["SECRET_KEY"].encode()
    return hmac.new(key, f"{email}:{purpose}:{code}".encode(), hashlib.sha256).hexdigest()


def _email_bodies(code: str, purpose: str, name: Optional[str], ttl: int):
    action = "create your account" if purpose == "register" else "sign in"
    greeting = f"Namaste {name}," if name else "Namaste,"
    text = (
        f"{greeting}\n\nYour CropVision AI code is: {code}\n\n"
        f"Enter it to {action}. It expires in {ttl} minutes.\n"
        "If you didn't ask for this code, you can ignore this email.\n\n— CropVision AI"
    )
    safe_greeting = html.escape(greeting)
    body = f"""\
<div style="font-family:Inter,Arial,sans-serif;max-width:480px;margin:auto;padding:24px;color:#163b1b">
  <div style="font-size:20px;font-weight:800;color:#1b5e20">🌱 CropVision AI</div>
  <p style="margin:20px 0 8px">{safe_greeting}</p>
  <p style="margin:0 0 16px">Use this code to {action}:</p>
  <div style="font-size:34px;font-weight:800;letter-spacing:10px;background:#e8f5e9;color:#1b5e20;
              padding:16px 20px;border-radius:12px;text-align:center">{code}</div>
  <p style="margin:16px 0 0;color:#628066;font-size:14px">It expires in {ttl} minutes.
     If you didn't ask for this code, you can ignore this email.</p>
</div>"""
    return text, body


def request_otp(email: str, purpose: str, data: Optional[Dict[str, Any]] = None) -> Dict[str, int]:
    cfg = current_app.config
    now = _now()

    existing = _col().find_one({"email": email, "purpose": purpose})
    if existing and existing["expires_at"] > now:
        wait = int(cfg["OTP_RESEND_SECONDS"] - (now - existing["created_at"]).total_seconds())
        if wait > 0:
            raise OtpError(
                "OTP_RESEND_WAIT", f"Please wait {wait} seconds before asking for a new code.", 429,
                {"retry_after_seconds": wait},
            )

    code = f"{secrets.randbelow(10 ** 6):06d}"
    ttl = cfg["OTP_TTL_MINUTES"]
    _col().replace_one(
        {"email": email, "purpose": purpose},
        {
            "email": email,
            "purpose": purpose,
            "code_hash": _hash(email, purpose, code),
            "attempts": 0,
            "data": data or {},
            "created_at": now,
            "expires_at": now + timedelta(minutes=ttl),
        },
        upsert=True,
    )

    text_body, html_body = _email_bodies(code, purpose, (data or {}).get("name"), ttl)
    send_email(email, f"{code} is your CropVision AI code", text_body, html_body)
    return {"expires_in_seconds": ttl * 60, "resend_in_seconds": cfg["OTP_RESEND_SECONDS"]}


def verify_otp(email: str, purpose: str, code: str) -> Dict[str, Any]:
    """Returns the data stored with the code (e.g. name/phone for sign up).
    The code is single-use: it's deleted on success or when attempts run out."""
    doc = _col().find_one({"email": email, "purpose": purpose})
    if not doc or doc["expires_at"] <= _now():
        raise OtpError("OTP_EXPIRED", "This code has expired. Please ask for a new one.", 400)

    max_attempts = current_app.config["OTP_MAX_ATTEMPTS"]
    if not hmac.compare_digest(doc["code_hash"], _hash(email, purpose, code)):
        attempts = doc.get("attempts", 0) + 1
        if attempts >= max_attempts:
            _col().delete_one({"_id": doc["_id"]})
            raise OtpError("OTP_TOO_MANY_ATTEMPTS", "Too many wrong codes. Please ask for a new one.", 429)
        _col().update_one({"_id": doc["_id"]}, {"$set": {"attempts": attempts}})
        left = max_attempts - attempts
        raise OtpError(
            "OTP_INVALID", f"That code is not correct. {left} {'try' if left == 1 else 'tries'} left.", 400,
            {"attempts_left": left},
        )

    _col().delete_one({"_id": doc["_id"]})
    return doc.get("data") or {}
