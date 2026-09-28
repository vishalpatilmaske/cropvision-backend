from functools import wraps

from flask_jwt_extended import get_jwt, verify_jwt_in_request

from app.utils.responses import error_response


def admin_required(fn):
    """Guards a route so only a token carrying the `is_admin` claim (issued
    by POST /api/admin/login) can call it -- a regular farmer's token is
    rejected even though it's otherwise valid."""

    @wraps(fn)
    def wrapper(*args, **kwargs):
        verify_jwt_in_request()
        claims = get_jwt()
        if not claims.get("is_admin"):
            return error_response("FORBIDDEN", "Admin access required.", 403)
        return fn(*args, **kwargs)

    return wrapper
