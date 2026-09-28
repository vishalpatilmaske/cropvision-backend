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


def _parse_origins(value: str) -> list:
    """"a.com, https://b.com/" -> ["a.com", "https://b.com"]: browsers send the
    Origin header without spaces or a trailing slash, so those would never match."""
    return [o.strip().rstrip("/") for o in value.split(",") if o.strip()]


def _allowed_origins(config) -> list:
    """Built-in frontends plus any extra ALLOWED_ORIGINS, without duplicates."""
    combined = _parse_origins(config["DEFAULT_ALLOWED_ORIGINS"] + "," + config["ALLOWED_ORIGINS"])
    return list(dict.fromkeys(combined))


def _ensure_indexes(app: Flask) -> None:
    """Create each collection's indexes on its own, so one failure (MongoDB down,
    or old duplicate data blocking a unique index) doesn't skip the rest."""
    from app.models.newsletter import NewsletterSubscriber
    from app.models.user import User
    from app.services import otp_service

    with app.app_context():
        for name, create in [
            ("users", User.ensure_indexes),
            ("otp_codes", otp_service.ensure_indexes),
            ("newsletter_subscribers", NewsletterSubscriber.ensure_indexes),
        ]:
            try:
                create()
            except Exception as exc:  # noqa: BLE001 - the app still starts; the log says what to fix
                app.logger.warning("Could not create %s indexes: %s", name, exc)


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
    _ensure_indexes(app)
    jwt.init_app(app)
    limiter.init_app(app)
    cors.init_app(
        app,
        resources={r"/api/*": {"origins": _allowed_origins(app.config)}},
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
