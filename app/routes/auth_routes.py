"""Farmer authentication: passwordless, with a one-time code sent by email.

    POST /api/auth/otp/request  {email, purpose: "login"|"register", name?, phone?}
    POST /api/auth/otp/verify   {email, purpose, code}  -> {user, access_token}
    GET  /api/auth/providers    -> {google_client_id} (null when Google sign-in is off)
    POST /api/auth/google       {access_token}  -> {user, access_token, created}

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
from app.services.google_auth_service import (
    GoogleAuthError,
    GoogleUnavailableError,
    verify_google_access_token,
)
from app.services.otp_service import PURPOSES, OtpError, request_otp, verify_otp
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
        if User.find_by_email(email):
            return error_response(
                "EMAIL_EXISTS", "An account with this email already exists. Please sign in instead.", 409
            )
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
        try:
            user = User.create(name=data.get("name"), email=email, phone=data.get("phone"))
        except DuplicateKeyError:
            return error_response("EMAIL_EXISTS", "An account with this email already exists.", 409)
        message, status = "Account created successfully.", 201

    token = create_access_token(identity=user.id)
    return success_response({"user": user.to_dict(), "access_token": token}, message, status)


@auth_bp.get("/providers")
def providers():
    """Which extra sign-in options the login page should show. The Google
    Client ID is public by design (it's embedded in every Google button)."""
    return success_response({"google_client_id": current_app.config.get("GOOGLE_CLIENT_ID") or None})


@auth_bp.post("/google")
@limiter.limit(lambda: current_app.config.get("RATE_LIMIT_OTP", "5 per minute"))
def google_sign_in():
    """Sign in (or sign up) with the access token from Google's sign-in popup.
    A new email gets an account straight away -- Google has already verified it."""
    if not current_app.config.get("GOOGLE_CLIENT_ID"):
        return error_response("GOOGLE_NOT_CONFIGURED", "Google sign-in is not available.", 503)

    google_token = (request.get_json(silent=True) or {}).get("access_token")
    if not isinstance(google_token, str) or not google_token:
        return error_response("VALIDATION_ERROR", "Missing Google sign-in token.", 400)

    try:
        profile = verify_google_access_token(google_token)
    except GoogleAuthError as exc:
        return error_response("GOOGLE_AUTH_FAILED", str(exc), 401)
    except GoogleUnavailableError as exc:
        return error_response("GOOGLE_UNAVAILABLE", str(exc), 502)

    user = User.find_by_email(profile["email"])
    created = False
    if not user:
        try:
            user = User.create(name=profile["name"], email=profile["email"])
            created = True
        except DuplicateKeyError:  # created by a parallel request a moment ago
            user = User.find_by_email(profile["email"])

    token = create_access_token(identity=user.id)
    logger.info("Google sign-in (new_account=%s).", created)
    return success_response(
        {"user": user.to_dict(), "access_token": token, "created": created},
        "Account created successfully." if created else "Login successful.",
        201 if created else 200,
    )


@auth_bp.get("/me")
@jwt_required()
def me():
    user_id = get_jwt_identity()
    user = User.find_by_id(user_id)
    if not user:
        return error_response("NOT_FOUND", "User not found.", 404)
    return success_response({"user": user.to_dict()})
