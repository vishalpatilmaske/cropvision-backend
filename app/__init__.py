import logging
import os

from flask import Flask
from werkzeug.middleware.proxy_fix import ProxyFix

from app.config import get_config
from app.extensions import cors, jwt, limiter, mongo
from app.services.ai.llm_client import llm_client
from app.utils.responses import error_response


def _configure_logging(app: Flask) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    # Never let werkzeug/urllib3 debug logs leak request bodies (which could
    # include Authorization headers or raw image data) at INFO level.
    logging.getLogger("werkzeug").setLevel(logging.WARNING)


_DEV_SECRETS = {"dev-secret-change-me", "dev-jwt-secret-change-me"}


def _check_production_config(app: Flask) -> None:
    """Refuse to start in production with the built-in development secrets --
    anyone could forge login tokens with them."""
    if os.getenv("FLASK_ENV") != "production":
        return
    weak = [
        name for name in ("SECRET_KEY", "JWT_SECRET_KEY")
        if app.config[name] in _DEV_SECRETS or len(app.config[name]) < 32
    ]
    if weak:
        raise RuntimeError(f"Set strong values (32+ chars) for {', '.join(weak)} in backend/.env.")
    if app.config["ADMIN_PASSWORD"] in {"123456", "change-me"}:
        app.logger.warning("ADMIN_PASSWORD is a default value -- change it in backend/.env.")


def create_app(config_object=None):
    app = Flask(__name__)
    app.config.from_object(config_object or get_config())

    _configure_logging(app)
    _check_production_config(app)

    if app.config.get("TRUST_PROXY"):
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    mongo.init_app(app)
    try:
        from app.services.otp_service import ensure_indexes

        with app.app_context():
            ensure_indexes()
    except Exception:  # noqa: BLE001 - Mongo may be down at boot; the app still starts
        app.logger.warning("Could not create otp_codes indexes (is MongoDB running?).")
    jwt.init_app(app)
    limiter.init_app(app)
    cors.init_app(
        app,
        resources={r"/api/*": {"origins": app.config["ALLOWED_ORIGINS"].split(",")}},
        supports_credentials=True,
    )

    with app.app_context():
        llm_client.init_app(app)

    from app.routes import register_blueprints

    register_blueprints(app)

    @app.get("/health")
    def health():
        """Liveness + dependency check for uptime monitors: 200 when the
        database answers, 503 when it doesn't. Reveals no secrets."""
        try:
            mongo.client.admin.command("ping")
            database = "ok"
        except Exception:  # noqa: BLE001 - any failure means "unreachable"
            app.logger.warning("Health check: database unreachable.")
            database = "unreachable"

        healthy = database == "ok"
        body = {
            "status": "ok" if healthy else "degraded",
            "database": database,
            "ai_configured": llm_client.is_available,
            "email_configured": bool(app.config.get("SMTP_USER") and app.config.get("SMTP_PASS")),
            "google_sign_in": bool(app.config.get("GOOGLE_CLIENT_ID")),
        }
        return body, 200 if healthy else 503

    @app.errorhandler(404)
    def not_found(_):
        return error_response("NOT_FOUND", "The requested resource was not found.", 404)

    @app.errorhandler(413)
    def too_large(_):
        return error_response("FILE_TOO_LARGE", "Uploaded file exceeds the maximum allowed size.", 413)

    @app.errorhandler(429)
    def rate_limited(_):
        return error_response("RATE_LIMITED", "Too many requests. Please slow down and try again.", 429)

    @app.errorhandler(Exception)
    def handle_unexpected(exc):
        from werkzeug.exceptions import HTTPException

        if isinstance(exc, HTTPException):
            return error_response(exc.name.upper().replace(" ", "_"), exc.description, exc.code)

        app.logger.exception("Unhandled exception: %s", type(exc).__name__)
        return error_response("INTERNAL_ERROR", "An unexpected error occurred. Please try again later.", 500)

    @jwt.unauthorized_loader
    def unauthorized(_reason):
        return error_response("UNAUTHORIZED", "Authentication is required for this action.", 401)

    @jwt.invalid_token_loader
    def invalid_token(_reason):
        return error_response("INVALID_TOKEN", "Invalid or expired authentication token.", 401)

    @jwt.expired_token_loader
    def expired_token(_header, _payload):
        return error_response("TOKEN_EXPIRED", "Your session has expired. Please log in again.", 401)

    return app
