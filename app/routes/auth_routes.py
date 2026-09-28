"""Farmer authentication: passwordless, with a one-time code sent by email.

    POST /api/auth/otp/request  {email, purpose: "login"|"register", name?, phone?}
    POST /api/auth/otp/verify   {email, purpose, code}  -> {user, access_token}

(The admin panel keeps its own fixed-credential login in admin_routes.py.)
"""
import logging
import re

from flask import Blueprint, current_app, request
from flask_jwt_extended import create_access_token, get_jwt_identity, jwt_required
from pymongo.errors import DuplicateKeyError

from app.extensions import limiter
from app.models.user import User
from app.services.email_service import EmailError
from app.services.otp_service import PURPOSES, OtpError, request_otp, verify_otp
from app.utils.contact_checks import suggest_email_fix
from app.utils.responses import error_response, success_response

logger = logging.getLogger("cropvision.routes.auth")

auth_bp = Blueprint("auth", __name__, url_prefix="/api/auth")

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def is_valid_email(email: str) -> bool:
    return bool(_EMAIL_RE.match(email)) and len(email) <= 254


def _read_email_and_purpose(payload):
    email = str(payload.get("email") or "").strip().lower()
    purpose = str(payload.get("purpose") or "").strip().lower()
    if not is_valid_email(email):
        raise OtpError("VALIDATION_ERROR", "Please enter a valid email address.", 400)
    if purpose not in PURPOSES:
        raise OtpError("VALIDATION_ERROR", "purpose must be 'login' or 'register'.", 400)
    return email, purpose


def email_typo_error(email: str):
    """400 with a suggestion when the domain is a common misspelling, else None."""
    suggestion = suggest_email_fix(email)
    if not suggestion:
        return None
    return error_response(
        "EMAIL_TYPO", f"Did you mean {suggestion}? Please check your email address.", 400,
        {"suggestion": suggestion},
    )


def phone_taken_error(phone, exclude_user_id=None):
    """409 when another account already uses this phone number, else None."""
    owner = User.find_by_phone(phone)
    if owner and owner.id != exclude_user_id:
        return error_response(
            "PHONE_EXISTS", "This phone number is already linked to another account.", 409
        )
    return None


def _otp_error(exc: OtpError):
    return error_response(exc.code, str(exc), exc.status, exc.details)


@auth_bp.post("/otp/request")
@limiter.limit(lambda: current_app.config.get("RATE_LIMIT_OTP", "5 per minute"))
def otp_request():
    payload = request.get_json(silent=True) or {}
    try:
        email, purpose = _read_email_and_purpose(payload)
    except OtpError as exc:
        return _otp_error(exc)

    data = {}
    if purpose == "login":
        if not User.find_by_email(email):
            suggestion = suggest_email_fix(email)
            if suggestion and User.find_by_email(suggestion):
                return error_response(
                    "ACCOUNT_NOT_FOUND", f"No account found with this email. Did you mean {suggestion}?", 404,
                    {"suggestion": suggestion},
                )
            return error_response(
                "ACCOUNT_NOT_FOUND", "No account found with this email. Please sign up first.", 404
            )
    else:
        name = (payload.get("name") or "").strip()
        phone = (payload.get("phone") or "").strip() or None
        if not name:
            return error_response("VALIDATION_ERROR", "Please enter your name.", 400)
        if len(name) > 120:
            return error_response("VALIDATION_ERROR", "Name is too long.", 400)
        typo = email_typo_error(email)
        if typo:
            return typo
        if User.find_by_email(email):
            return error_response(
                "EMAIL_EXISTS", "An account with this email already exists. Please sign in instead.", 409
            )
        taken = phone_taken_error(phone)
        if taken:
            return taken
        data = {"name": name, "phone": phone}

    try:
        timing = request_otp(email, purpose, data)
    except OtpError as exc:
        return _otp_error(exc)
    except EmailError:
        return error_response(
            "EMAIL_SEND_FAILED", "We couldn't send the code right now. Please try again in a moment.", 502
        )

    logger.info("Login code sent (purpose=%s).", purpose)
    return success_response({"email": email, "purpose": purpose, **timing}, f"We sent a 6-digit code to {email}.")


@auth_bp.post("/otp/verify")
@limiter.limit(lambda: current_app.config.get("RATE_LIMIT_OTP", "5 per minute"))
def otp_verify():
    payload = request.get_json(silent=True) or {}
    try:
        email, purpose = _read_email_and_purpose(payload)
    except OtpError as exc:
        return _otp_error(exc)

    code = re.sub(r"\s+", "", str(payload.get("code") or ""))
    if not re.fullmatch(r"\d{6}", code):
        return error_response("VALIDATION_ERROR", "Please enter the 6-digit code.", 400)

    try:
        data = verify_otp(email, purpose, code)
    except OtpError as exc:
        return _otp_error(exc)

    if purpose == "login":
        user = User.find_by_email(email)
        if not user:
            return error_response("ACCOUNT_NOT_FOUND", "No account found with this email.", 404)
        message, status = "Login successful.", 200
    else:
        if User.find_by_email(email):
            return error_response("EMAIL_EXISTS", "An account with this email already exists.", 409)
        taken = phone_taken_error(data.get("phone"))
        if taken:
            return taken
        try:
            user = User.create(name=data.get("name"), email=email, phone=data.get("phone"))
        except DuplicateKeyError as exc:  # a parallel sign-up won the race
            if "phone_key" in str(exc):
                return error_response("PHONE_EXISTS", "This phone number is already linked to another account.", 409)
            return error_response("EMAIL_EXISTS", "An account with this email already exists.", 409)
        message, status = "Account created successfully.", 201

    token = create_access_token(identity=user.id)
    return success_response({"user": user.to_dict(), "access_token": token}, message, status)


@auth_bp.get("/me")
@jwt_required()
def me():
    user_id = get_jwt_identity()
    user = User.find_by_id(user_id)
    if not user:
        return error_response("NOT_FOUND", "User not found.", 404)
    return success_response({"user": user.to_dict()})
