import hmac
import logging

from flask import Blueprint, current_app, request
from flask_jwt_extended import create_access_token

from app.extensions import limiter
from app.models.disease_pest_prediction import DiseasePestPrediction
from app.models.user import User
from app.routes.auth_routes import is_valid_email
from app.utils.admin_auth import admin_required
from app.utils.pagination import get_pagination_params, paginated_response
from app.utils.responses import error_response, success_response

logger = logging.getLogger("cropvision.routes.admin")

admin_bp = Blueprint("admin", __name__, url_prefix="/api/admin")


@admin_bp.post("/login")
@limiter.limit(lambda: current_app.config.get("RATE_LIMIT_OTP", "5 per minute"))
def admin_login():
    payload = request.get_json(silent=True) or {}
    email = str(payload.get("email") or "").strip().lower()
    password = str(payload.get("password") or "")

    admin_email = current_app.config["ADMIN_EMAIL"].strip().lower()
    admin_password = current_app.config["ADMIN_PASSWORD"]

    # compare_digest on both so response time doesn't reveal which one was wrong.
    email_ok = hmac.compare_digest(email.encode(), admin_email.encode())
    password_ok = hmac.compare_digest(password.encode(), admin_password.encode())
    if not (email and password and email_ok and password_ok):
        return error_response("INVALID_CREDENTIALS", "Invalid admin email or password.", 401)

    token = create_access_token(identity=admin_email, additional_claims={"is_admin": True})
    logger.info("Admin login successful.")
    return success_response(
        {"admin": {"email": admin_email}, "access_token": token}, "Admin login successful."
    )


@admin_bp.get("/stats")
@admin_required
def stats():
    return success_response({
        "total_users": User.count(),
        "total_analyses": DiseasePestPrediction.count(),
    })


@admin_bp.get("/users")
@admin_required
def list_users():
    page, per_page = get_pagination_params()
    search = request.args.get("search")
    items, total = User.find_all(page=page, per_page=per_page, search=search)
    return success_response(paginated_response(items, total, page, per_page, lambda u: u.to_dict()))


@admin_bp.get("/users/<user_id>")
@admin_required
def get_user(user_id):
    user = User.find_by_id(user_id)
    if not user:
        return error_response("NOT_FOUND", "User not found.", 404)
    return success_response({"user": user.to_dict()})


@admin_bp.post("/users")
@admin_required
def create_user():
    payload = request.get_json(silent=True) or {}
    name = (payload.get("name") or "").strip()
    email = (payload.get("email") or "").strip().lower()
    phone = (payload.get("phone") or "").strip() or None

    if not name or not email:
        return error_response("VALIDATION_ERROR", "name and email are required.", 400)
    if not is_valid_email(email):
        return error_response("VALIDATION_ERROR", "Please enter a valid email address.", 400)
    if User.find_by_email(email):
        return error_response("EMAIL_EXISTS", "A user with this email already exists.", 409)

    # The farmer signs in with an emailed code, so no password is set here.
    user = User.create(name=name, email=email, phone=phone)
    logger.info("Admin created user %s", user.id)
    return success_response({"user": user.to_dict()}, "User created.", 201)


@admin_bp.put("/users/<user_id>")
@admin_required
def update_user(user_id):
    user = User.find_by_id(user_id)
    if not user:
        return error_response("NOT_FOUND", "User not found.", 404)

    payload = request.get_json(silent=True) or {}
    fields = {}

    if "name" in payload:
        name = (payload.get("name") or "").strip()
        if not name:
            return error_response("VALIDATION_ERROR", "name cannot be empty.", 400)
        fields["name"] = name

    if "email" in payload:
        email = (payload.get("email") or "").strip().lower()
        if not is_valid_email(email):
            return error_response("VALIDATION_ERROR", "Please enter a valid email address.", 400)
        existing = User.find_by_email(email)
        if existing and existing.id != user.id:
            return error_response("EMAIL_EXISTS", "Another user already uses this email.", 409)
        fields["email"] = email

    if "phone" in payload:
        fields["phone"] = (payload.get("phone") or "").strip() or None

    if fields:
        user.update(fields)

    logger.info("Admin updated user %s", user.id)
    return success_response({"user": user.to_dict()}, "User updated.")


@admin_bp.delete("/users/<user_id>")
@admin_required
def delete_user(user_id):
    user = User.find_by_id(user_id)
    if not user:
        return error_response("NOT_FOUND", "User not found.", 404)
    User.delete_by_id(user_id)
    logger.info("Admin deleted user %s", user_id)
    return success_response({}, "User deleted.")
