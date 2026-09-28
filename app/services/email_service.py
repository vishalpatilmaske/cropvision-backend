"""Outgoing email over SMTP (e.g. Gmail with an app password).

Settings come from SMTP_HOST / SMTP_PORT / SMTP_USER / SMTP_PASS. Port 465
uses implicit TLS; any other port uses STARTTLS. With MAIL_SUPPRESS_SEND
(the test config) messages are appended to `outbox` instead of being sent.
"""
import logging
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr
from typing import Dict, List, Optional

from flask import current_app

logger = logging.getLogger("cropvision.services.email")

outbox: List[Dict[str, str]] = []


class EmailError(Exception):
    pass


def send_email(to: str, subject: str, text: str, html: Optional[str] = None) -> None:
    cfg = current_app.config

    if cfg.get("MAIL_SUPPRESS_SEND"):
        outbox.append({"to": to, "subject": subject, "text": text, "html": html or ""})
        return

    host, user, password = cfg.get("SMTP_HOST"), cfg.get("SMTP_USER"), cfg.get("SMTP_PASS")
    if not (host and user and password):
        logger.error("SMTP is not configured (SMTP_HOST / SMTP_USER / SMTP_PASS).")
        raise EmailError("Email is not configured.")

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = formataddr((cfg.get("MAIL_FROM_NAME", "CropVision AI"), cfg.get("MAIL_FROM") or user))
    msg["To"] = to
    msg.set_content(text)
    if html:
        msg.add_alternative(html, subtype="html")

    port = int(cfg.get("SMTP_PORT", 587))
    context = ssl.create_default_context()
    try:
        if port == 465:
            with smtplib.SMTP_SSL(host, port, context=context, timeout=15) as smtp:
                smtp.login(user, password)
                smtp.send_message(msg)
        else:
            with smtplib.SMTP(host, port, timeout=15) as smtp:
                smtp.starttls(context=context)
                smtp.login(user, password)
                smtp.send_message(msg)
    except (smtplib.SMTPException, OSError) as exc:
        # Never log the password or message body.
        logger.error("Failed to send email: %s", type(exc).__name__)
        raise EmailError("Could not send email.") from exc
