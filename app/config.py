"""Application configuration loaded from environment variables."""
import os
from datetime import timedelta

from dotenv import load_dotenv

load_dotenv()


def _bool_env(name: str, default: bool) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in {"1", "true", "yes", "on"}


class Config:
    # --- Core ---
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-change-me")
    JWT_SECRET_KEY = os.getenv("JWT_SECRET", os.getenv("JWT_SECRET_KEY", "dev-jwt-secret-change-me"))
    JWT_ACCESS_TOKEN_EXPIRES = timedelta(hours=int(os.getenv("JWT_ACCESS_TOKEN_EXPIRES_HOURS", "12")))
    JWT_TOKEN_LOCATION = ["headers"]

    # --- Database (MongoDB, local instance) ---
    # MONGODB_URI is accepted too -- it's the name Atlas and most hosts use.
    MONGO_URI = os.getenv("MONGO_URI") or os.getenv("MONGODB_URI") or "mongodb://localhost:27017/cropvision"

    # --- Behind a reverse proxy (Nginx): trust its X-Forwarded-For / -Proto
    #     headers so rate limits see each visitor's real IP. ---
    TRUST_PROXY = _bool_env("TRUST_PROXY", False)

    # --- CORS ---
    # Our own frontends are always allowed. ALLOWED_ORIGINS (comma-separated)
    # adds more, e.g. a custom domain -- it never removes these.
    DEFAULT_ALLOWED_ORIGINS = "http://localhost:5173,https://cropvision-frontend.vercel.app"
    ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "")

    # --- Vision LLM (OpenAI GPT; disease/pest detection and the text-based
    #     advisory features are NOT a locally trained model, they are hosted
    #     LLM calls -- see app/services/ai/llm_client.py). ---
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
    OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o")
    AI_REQUEST_TIMEOUT_SECONDS = int(os.getenv("AI_REQUEST_TIMEOUT_SECONDS", "30"))
    AI_MAX_RETRIES = int(os.getenv("AI_MAX_RETRIES", "2"))

    # --- Weather ---
    WEATHER_API_URL = os.getenv("WEATHER_API_URL", "https://api.open-meteo.com/v1/forecast")
    GEOCODING_API_URL = os.getenv("GEOCODING_API_URL", "https://geocoding-api.open-meteo.com/v1/search")
    CLIMATE_API_URL = os.getenv("CLIMATE_API_URL", "https://archive-api.open-meteo.com/v1/archive")

    # --- Uploads / cost control ---
    MAX_UPLOAD_SIZE_BYTES = int(os.getenv("MAX_UPLOAD_SIZE_BYTES", str(8 * 1024 * 1024)))  # 8 MB
    MAX_IMAGE_DIMENSION_PX = int(os.getenv("MAX_IMAGE_DIMENSION_PX", "1600"))
    ALLOWED_IMAGE_MIME_TYPES = {"image/jpeg", "image/jpg", "image/png", "image/webp"}
    ALLOWED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}

    # --- Rate limiting ---
    # Shared store so every Gunicorn worker counts the same requests
    # (e.g. mongodb://localhost:27017). "memory://" is fine for local dev.
    RATELIMIT_STORAGE_URI = os.getenv("RATELIMIT_STORAGE_URI", "memory://")
    RATE_LIMIT_ANALYZE = os.getenv("RATE_LIMIT_ANALYZE", "10 per minute")
    RATE_LIMIT_CHAT = os.getenv("RATE_LIMIT_CHAT", "20 per minute")

    MAX_CONTENT_LENGTH = MAX_UPLOAD_SIZE_BYTES + 1024 * 1024  # headroom for form fields

    # --- Email (SMTP) for login codes ---
    SMTP_HOST = os.getenv("SMTP_HOST", "")
    SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
    SMTP_USER = os.getenv("SMTP_USER", "")
    SMTP_PASS = os.getenv("SMTP_PASS", "")
    MAIL_FROM = os.getenv("MAIL_FROM", "") or SMTP_USER
    MAIL_FROM_NAME = os.getenv("MAIL_FROM_NAME", "CropVision AI")
    MAIL_SUPPRESS_SEND = _bool_env("MAIL_SUPPRESS_SEND", False)

    # --- One-time login codes (farmer sign in / sign up) ---
    OTP_TTL_MINUTES = int(os.getenv("OTP_TTL_MINUTES", "10"))
    OTP_MAX_ATTEMPTS = int(os.getenv("OTP_MAX_ATTEMPTS", "5"))
    OTP_RESEND_SECONDS = int(os.getenv("OTP_RESEND_SECONDS", "60"))
    RATE_LIMIT_OTP = os.getenv("RATE_LIMIT_OTP", "5 per minute")

    # --- Admin (fixed credential, not stored in the database) ---
    ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "admin@gmail.com")
    ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "123456")


class TestConfig(Config):
    TESTING = True
    MONGO_URI = "mongodb://localhost:27017/cropvision_test"
    JWT_SECRET_KEY = "test-jwt-secret-key-for-automated-tests-only"
    MAIL_SUPPRESS_SEND = True  # emails go to app.services.email_service.outbox
    RATE_LIMIT_OTP = "1000 per minute"


def get_config():
    env = os.getenv("FLASK_ENV", "development")
    if env == "testing":
        return TestConfig
    return Config
