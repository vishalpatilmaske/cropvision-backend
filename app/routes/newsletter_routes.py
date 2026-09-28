"""Landing-page newsletter.

    POST /api/newsletter/subscribe            {email}  -> {status: "subscribed"|"already_subscribed"}
    GET  /api/newsletter/unsubscribe?email=&token=     -> small HTML confirmation page

The unsubscribe link is signed (HMAC of the email with SECRET_KEY), so nobody
can unsubscribe someone else by guessing the URL.
"""
import hashlib
import hmac
import html
import logging
from urllib.parse import urlencode

from flask import Blueprint, current_app, request

from app.extensions import limiter
from app.models.newsletter import NewsletterSubscriber
from app.routes.auth_routes import is_valid_email
from app.services.email_service import EmailError, send_email
from app.utils.responses import error_response, success_response

logger = logging.getLogger("cropvision.routes.newsletter")

newsletter_bp = Blueprint("newsletter", __name__, url_prefix="/api/newsletter")


def unsubscribe_token(email: str) -> str:
    key = current_app.config["SECRET_KEY"].encode()
    return hmac.new(key, f"newsletter:{email}".encode(), hashlib.sha256).hexdigest()


def _unsubscribe_url(email: str) -> str:
    query = urlencode({"email": email, "token": unsubscribe_token(email)})
    return f"{request.host_url.rstrip('/')}/api/newsletter/unsubscribe?{query}"


def _send_welcome(email: str) -> None:
    """Best-effort: the subscription stands even if the email can't be sent."""
    link = _unsubscribe_url(email)
    text = (
        "Namaste,\n\nThanks for subscribing to CropVision AI updates. We'll send you seasonal crop "
        "tips, disease alerts and new features -- no spam.\n\n"
        f"Don't want these emails? Unsubscribe: {link}\n\n— CropVision AI"
    )
    body = f"""\
<div style="font-family:Inter,Arial,sans-serif;max-width:480px;margin:auto;padding:24px;color:#163b1b">
  <div style="font-size:20px;font-weight:800;color:#1b5e20">🌱 CropVision AI</div>
  <p style="margin:20px 0 8px">Namaste,</p>
  <p style="margin:0 0 16px">Thanks for subscribing! We'll send you seasonal crop tips, disease alerts
     and new features — no spam.</p>
  <p style="margin:24px 0 0;color:#628066;font-size:13px">Don't want these emails?
     <a href="{html.escape(link)}" style="color:#2e8b4e">Unsubscribe</a>.</p>
</div>"""
    try:
        send_email(email, "Welcome to CropVision AI updates 🌱", text, body)
    except EmailError:
        logger.warning("Newsletter welcome email could not be sent.")


@newsletter_bp.post("/subscribe")
@limiter.limit(lambda: current_app.config.get("RATE_LIMIT_NEWSLETTER", "5 per minute"))
def subscribe():
    email = str((request.get_json(silent=True) or {}).get("email") or "").strip().lower()
    if not is_valid_email(email):
        return error_response("VALIDATION_ERROR", "Please enter a valid email address.", 400)

    status, is_new = NewsletterSubscriber.subscribe(email)
    if not is_new:
        return success_response({"status": status}, "You're already subscribed. Thank you!")

    _send_welcome(email)
    logger.info("Newsletter subscription added.")
    return success_response({"status": status}, "Thanks for subscribing! Check your inbox.", 201)


def _page(title: str, message: str, status: int = 200):
    page = f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{html.escape(title)}</title></head>
<body style="margin:0;min-height:100vh;display:grid;place-items:center;background:#f5f9f5;
font-family:Inter,Arial,sans-serif;color:#163b1b">
<div style="max-width:420px;margin:24px;padding:32px;background:#fff;border-radius:16px;text-align:center;
box-shadow:0 20px 50px rgba(27,94,32,.12)">
<div style="font-size:36px">🌱</div><h1 style="font-size:22px;margin:12px 0 8px">{html.escape(title)}</h1>
<p style="color:#628066;margin:0">{html.escape(message)}</p></div></body></html>"""
    return page, status, {"Content-Type": "text/html; charset=utf-8"}


@newsletter_bp.get("/unsubscribe")
def unsubscribe():
    email = (request.args.get("email") or "").strip().lower()
    token = request.args.get("token") or ""
    if not email or not hmac.compare_digest(token, unsubscribe_token(email)):
        return _page("Link not valid", "This unsubscribe link is incomplete or has expired.", 400)
    NewsletterSubscriber.unsubscribe(email)
    return _page("You're unsubscribed", "You won't receive CropVision AI newsletter emails any more.")
